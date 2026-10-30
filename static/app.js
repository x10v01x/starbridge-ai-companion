"use strict";
const $ = (s) => document.querySelector(s);
const state = { user: null, view: "overview", models: [], page: 1, total: 0, config: null, public: null, refreshing: false };
const number = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(Number(value) || 0);
const points = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Math.trunc(Number(value) || 0));
let toastTimer;

function toast(text, error = false) {
  const el = $("#toast"); el.textContent = text; el.classList.toggle("error", error); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
}
async function api(path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) { headers["Content-Type"] = "application/json"; headers["X-Portal-Request"] = "1"; }
  let response;
  try { response = await fetch(`/api/${path}`, { method: body === undefined ? "GET" : "POST", credentials: "same-origin", headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(25000) }); }
  catch { throw new Error("网络连接暂不可用，请检查网络后重试。"); }
  let data;
  try { data = await response.json(); } catch { throw new Error("服务暂不可用，请稍后重试。"); }
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "提交内容不正确，请检查后重试。");
    error.status = response.status; throw error;
  }
  return data;
}
function loginDialog() { $("#login-error").textContent = ""; $("#login-dialog").showModal(); $("#username").focus(); }
function clearUser() {
  state.user = null; state.models = []; state.config = null;
  $("#manual-json").value = ""; $("#manual-result").hidden = true;
  $("#workspace").hidden = true; $("#landing").hidden = false;
  $("#account-area").replaceChildren();
  const b = document.createElement("button"); b.className = "button dark small"; b.textContent = "登录账户 ↗";
  b.addEventListener("click", loginDialog); $("#account-area").append(b);
  document.title = `${state.public?.brand || "星桥 AI"} · 让好用的 AI，触手可及`;
}
function renderUser(user) {
  state.user = user; document.querySelectorAll(".admin-pricing-link").forEach(el => el.hidden = !user.is_admin); $("#landing").hidden = true; $("#workspace").hidden = false;
  $("#greeting").textContent = `欢迎回来，${user.display_name}`;
  $("#balance").textContent = points(user.points); $("#balance").title = `精确余额：${number(user.points)} 积分`; $("#used").textContent = points(user.used_points); $("#used").title = `精确消耗：${number(user.used_points)} 积分`; $("#requests").textContent = number(user.requests);
  $("#updated-at").textContent = `更新于 ${new Date(user.updated_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })}`;
  $("#sync-label").textContent = "余额已同步"; $("#low-balance").hidden = user.balance > 0;
  $("#test-banner").hidden = !state.public?.test_pricing;
  const account = $("#account-area"); account.replaceChildren();
  const button = document.createElement("button"); button.className = "account-button";
  const avatar = document.createElement("span"); avatar.className = "avatar"; avatar.textContent = user.username.slice(0, 1).toUpperCase();
  button.append(avatar, document.createTextNode(user.username)); button.addEventListener("click", () => setView("overview"));
  const out = document.createElement("button"); out.className = "logout-link"; out.textContent = "退出";
  out.addEventListener("click", async () => { out.disabled = true; try { await api("logout", {}); clearUser(); } catch (e) { toast(e.message, true); out.disabled = false; } });
  account.append(button, out);
  document.title = `我的空间 · ${state.public?.brand || "星桥 AI"}`;
}
async function refreshBalance(silent = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  const refresh = $("#refresh-balance"); if (!silent) { refresh.disabled = true; refresh.textContent = "同步中…"; $("#sync-label").textContent = "正在同步余额"; }
  try { renderUser(await api("me")); if (!silent) toast("余额已更新"); }
  catch (e) {
    if (e.status === 401 || e.status === 403) { clearUser(); if (!silent) loginDialog(); }
    else $("#sync-label").textContent = "同步失败，显示上次数据";
    if (!silent) toast(e.message, true);
  } finally { state.refreshing = false; refresh.disabled = false; refresh.textContent = "↻ 刷新"; }
}
async function setView(view) {
  if (!["overview", "connect", "recharge", "usage"].includes(view)) return;
  state.view = view;
  if (!state.user) { loginDialog(); return; }
  document.querySelectorAll(".view").forEach((el) => { el.hidden = el.id !== `view-${view}`; });
  document.querySelectorAll(".side-link").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  if (view === "connect") await loadModels();
  if (view === "usage") await loadUsage();
}
async function loadModels() {
  const button = $("#reload-models"), status = $("#models-status"); button.disabled = true; button.textContent = "同步中…"; status.textContent = "正在获取可用模型…";
  try {
    const data = await api("models"); state.models = data.models;
    let prices = []; try { prices = (await api("pricing")).models; } catch {}
    const list = $("#model-list"); list.replaceChildren();
    if (!state.models.length) { status.textContent = "当前账户暂无可用模型，请联系服务方。"; return; }
    for (const model of state.models) {
      const card = document.createElement("article"); card.className = "model-card";
      const title = document.createElement("strong"); title.textContent = model.name;
      const id = document.createElement("code"); id.textContent = model.id;
      const tags = document.createElement("div"); tags.className = "model-tags";
      const labels = [model.recommended ? "已做基础验证" : null, model.tools ? "工具调用" : null, model.vision ? "图片输入" : null].filter(Boolean);
      for (const label of labels) { const span = document.createElement("span"); span.textContent = label; tags.append(span); }
      card.append(title, id, tags);
      const rate = prices.find(p => p.model_name === model.id);
      if (rate) { const p = document.createElement("p"); p.className = "model-rate"; p.textContent = rate.quota_type === 0 ? `输入倍率 ${number(rate.model_ratio)} · 输出相对倍率 ${number(rate.completion_ratio)}` : "按次计费 · 以服务端结算为准"; card.append(p); }
      list.append(card);
    }
    status.textContent = `已同步 ${state.models.length} 个可用模型`;
  } catch (e) { status.textContent = "模型同步失败，请重试。"; toast(e.message, true); } finally { button.disabled = false; button.textContent = "刷新列表 ↻"; }
}
async function loadUsage() {
  const button = $("#refresh-usage"), status = $("#usage-status"); button.disabled = true; button.textContent = "加载中…"; status.textContent = "正在获取消费记录…";
  try {
    const data = await api(`usage?page=${state.page}`); state.total = data.total;
    const tbody = $("#usage-body"); tbody.replaceChildren(); $("#usage-empty").hidden = data.items.length > 0;
    for (const row of data.items) {
      const tr = document.createElement("tr");
      const cells = [new Date(row.created_at * 1000).toLocaleString("zh-CN", { hour12: false }), row.type === 1 ? "兑换充值" : row.model_name || "其他记录", row.type === 2 ? `${number((row.prompt_tokens || 0) + (row.completion_tokens || 0))} Token${row.is_stream ? " · 流式" : ""}` : "充值记录", row.type === 2 ? `− ${points(row.quota / state.public.quota_per_point)}` : "—"];
      for (const value of cells) { const td = document.createElement("td"); td.textContent = value; tr.append(td); }
      tbody.append(tr);
    }
    status.textContent = "记录已更新"; $("#usage-total").textContent = `共 ${number(data.total)} 条 · 第 ${state.page} 页`;
    $("#prev-page").disabled = state.page <= 1; $("#next-page").disabled = state.page * 10 >= data.total;
  } catch (e) { status.textContent = "记录加载失败，请重试。"; toast(e.message, true); } finally { button.disabled = false; button.textContent = "刷新记录 ↻"; }
}

document.querySelectorAll("[data-view]").forEach((el) => el.addEventListener("click", () => setView(el.dataset.view)));
$("#open-login").addEventListener("click", loginDialog); $("#hero-login").addEventListener("click", loginDialog);
$("#close-login").addEventListener("click", () => $("#login-dialog").close());
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const submit = event.currentTarget.querySelector('[type="submit"]'); submit.disabled = true;
  $("#login-error").textContent = "";
  try {
    const result = await api(registering ? "register" : "login", { username: $("#username").value.trim(), password: $("#password").value, code: $("#two-factor").value.trim(), remember: $("#remember-login").checked });
    if (result.requires_2fa) { $("#two-factor-field").hidden = false; $("#two-factor").focus(); return; }
    $("#password").value = ""; $("#two-factor").value = ""; $("#two-factor-field").hidden = true;
    $("#login-dialog").close(); renderUser(result.user); await setView(state.view); await desktopApproval();
  } catch (e) { $("#login-error").textContent = e.message; }
  finally { submit.disabled = false; }
});
$("#redeem-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
  const message = $("#redeem-message"); message.textContent = "正在核验兑换码，请稍候…"; message.className = "";
  try { const data = await api("redeem", { code: $("#redeem-code").value.trim() }); renderUser(data.user); $("#redeem-code").value = ""; message.className = "success-text"; message.textContent = `兑换成功，到账 ${points(data.credited / state.public.quota_per_point)} 积分。`; }
  catch (e) { message.className = "error-text"; message.textContent = e.message; }
  finally { button.disabled = false; }
});
$("#refresh-balance").addEventListener("click", () => refreshBalance());
$("#reload-models").addEventListener("click", loadModels); $("#refresh-usage").addEventListener("click", loadUsage);
$("#prev-page").addEventListener("click", () => { if (state.page > 1) { state.page--; loadUsage(); } });
$("#next-page").addEventListener("click", () => { if (state.page * 10 < state.total) { state.page++; loadUsage(); } });
$("#prepare-config").addEventListener("click", async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try {
    if (!state.models.length) await loadModels();
    if (!state.models.length) throw new Error("当前暂无可配置模型。");
    state.config = await api("configuration", { models: state.models.map((m) => m.id) });
    $("#manual-json").value = JSON.stringify(state.config.models, null, 2); $("#manual-result").hidden = false;
    toast("个人配置已生成，请勿分享其中的密钥。");
  } catch (e) { toast(e.message, true); } finally { button.disabled = false; }
});
$("#download-json").addEventListener("click", () => {
  if (!state.config) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.config.models, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = "models-personal.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function start() {
  try {
    state.public = await api("public"); document.querySelectorAll("[data-brand]").forEach((el) => { el.textContent = state.public.brand; });
    await refreshBalance(true);
    if (state.user) { await setView(location.hash === "#recharge" ? "recharge" : "overview"); await desktopApproval(); } else if (desktopId) loginDialog();
  } catch (e) { toast(e.message, true); }
  setInterval(() => { if (state.user && !document.hidden) refreshBalance(true); }, Math.max(10, state.public?.refresh_seconds || 20) * 1000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.user) refreshBalance(true); });

let registering = false;
const desktopId = new URLSearchParams(location.search).get("desktop");
$("#toggle-register").addEventListener("click", () => {
  registering = !registering;
  $("#auth-title").textContent = registering ? "创建你的 AI 账户" : "登录你的 AI 空间";
  $("#auth-description").textContent = registering ? "账号 3–20 位字母、数字或下划线；密码 10–20 位。注册后自动准备个人接入凭证。" : "登录一次，网页与客户端轻松连接。";
  $("#auth-submit-text").textContent = registering ? "注册并继续 ↗" : "登录并继续 ↗";
  $("#toggle-register").textContent = registering ? "已有账号，去登录" : "立即注册";
  $("#password").autocomplete = registering ? "new-password" : "current-password";
  $("#password").minLength = registering ? 10 : 1;
  $("#password").maxLength = registering ? 20 : 128;
  $("#two-factor-field").hidden = true;
  $("#login-error").textContent = "";
});
async function desktopApproval() {
  if (!desktopId || !state.user || sessionStorage.getItem("approved:" + desktopId)) return;
  try { const request = await api("desktop/request?id=" + encodeURIComponent(desktopId)); $("#desktop-code").textContent = request.code; $("#desktop-dialog").showModal(); }
  catch(e) { toast(e.message, true); }
}
$("#approve-desktop").addEventListener("click", async () => {
  const button = $("#approve-desktop"); button.disabled = true;
  try { await api("desktop/approve", {id:desktopId}); sessionStorage.setItem("approved:"+desktopId,"1"); $("#desktop-message").textContent="登录已确认，可以返回客户端。"; button.textContent="已允许，请返回助手 ✓"; }
  catch(e) { $("#desktop-message").textContent=e.message; button.disabled=false; }
});
$("#cancel-desktop").addEventListener("click", () => $("#desktop-dialog").close());

start();
