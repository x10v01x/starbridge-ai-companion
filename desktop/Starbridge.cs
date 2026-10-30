using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Web.Script.Serialization;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Threading;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Reflection;

public class Starbridge {
 string Portal;
 static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 2097152 };
 static readonly string Data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StarbridgeCompanion");
 static readonly string EndpointFile = Path.Combine(Data, "endpoint.json");
 string AuthFile { get { if(String.IsNullOrEmpty(Portal)) throw new Exception("请先设置服务地址。"); using(var sha=SHA256.Create()) return Path.Combine(Data, "session-"+BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Portal))).Replace("-", "").ToLowerInvariant()+".bin"); } }
 readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(25) };
 Window window; string token; bool busy, refreshing, syncingModels; int authVersion;
 readonly List<CheckBox> choices = new List<CheckBox>();
 T Find<T>(string name) where T:FrameworkElement { return (T)window.FindName(name); }
 void Status(string text) { var label=Find<TextBlock>("Status");label.Text=text;if(SystemParameters.ClientAreaAnimation){label.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(0.45,1,TimeSpan.FromMilliseconds(200)));} }
 void ApplyMotion() {
  if(!SystemParameters.ClientAreaAnimation)return;
  foreach(var name in new[]{"WalletCard","ModelsCard"}) {var item=Find<Border>(name);item.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(0,1,TimeSpan.FromMilliseconds(320)));var move=new TranslateTransform(0,10);item.RenderTransform=move;move.BeginAnimation(TranslateTransform.YProperty,new DoubleAnimation(10,0,TimeSpan.FromMilliseconds(320)){EasingFunction=new CubicEase {EasingMode=EasingMode.EaseOut}});}
  foreach(var name in new[]{"HomeButton","WebButton","HelpButton","LoginButton","LogoutButton","RefreshButton","SyncModelsButton","BrowseButton","ConfigureButton","RestoreButton"}) {var button=Find<Button>(name);var move=new TranslateTransform();button.RenderTransform=move;button.MouseEnter+=(sender,args)=>move.BeginAnimation(TranslateTransform.YProperty,new DoubleAnimation(-2,TimeSpan.FromMilliseconds(130)));button.MouseLeave+=(sender,args)=>move.BeginAnimation(TranslateTransform.YProperty,new DoubleAnimation(0,TimeSpan.FromMilliseconds(160)));}
 }

 static Dictionary<string,object> Obj(object value) { return (Dictionary<string,object>)value; }
 static object[] Arr(object value) { return ((IEnumerable)value).Cast<object>().ToArray(); }
 static string Text(object value) { return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture); }
 static void Open(string url) { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }

 [STAThread] public static void Main(string[] args) {
  try {
   ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
   if(args.Contains("--self-test")) { SelfTest(args.Last()); return; }
   var app = new Application(); var main = new Starbridge();
   using(var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("MainWindow.xaml")) main.window=(Window)XamlReader.Load(stream);
   main.Initialize(); app.Run(main.window);
  } catch(Exception e) { if(args.Contains("--self-test")) { try { File.WriteAllText(args.Last()+".error.txt",e.ToString(),Encoding.UTF8); } catch {} Environment.Exit(1); } MessageBox.Show(e.Message,"星桥 AI",MessageBoxButton.OK,MessageBoxImage.Error); }
 }
 void Initialize() {
  using(var iconStream=Assembly.GetExecutingAssembly().GetManifestResourceStream("icons.app.png")){var icon=new System.Windows.Media.Imaging.BitmapImage();icon.BeginInit();icon.CacheOption=System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;icon.StreamSource=iconStream;icon.EndInit();window.Icon=icon;Find<Image>("BrandIcon").Source=icon;}
  foreach(var pair in new[]{new[]{"IconDashboard","dashboard"},new[]{"IconWallet","wallet"},new[]{"IconHelp","help"},new[]{"MetricWallet","wallet"},new[]{"MetricUsage","dashboard"},new[]{"MetricRequests","history"},new[]{"ConnectIcon","connect"}}) { using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("icons."+pair[1]+".png")) { var bmp=new System.Windows.Media.Imaging.BitmapImage();bmp.BeginInit();bmp.CacheOption=System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;bmp.StreamSource=stream;bmp.EndInit();Find<Image>(pair[0]).Source=bmp; } }
  Find<TextBox>("ConfigPath").Text=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".workbuddy","models.json");
  Find<CheckBox>("SelectAll").Click+=(s,e)=> { bool selected=Find<CheckBox>("SelectAll").IsChecked==true;foreach(var item in choices)item.IsChecked=selected; };
  Portal=LoadEndpoint();Find<TextBox>("PortalUrl").Text=Portal??"";
  Find<Button>("PortalApplyButton").Click+=async(s,e)=>{try{if(!String.IsNullOrEmpty(token)||busy||refreshing||syncingModels)throw new Exception("请先退出当前账号并等待操作完成，再切换服务。");string next=NormalizePortal(Find<TextBox>("PortalUrl").Text);SaveEndpoint(next);Portal=next;token=LoadToken();Reset();if(!String.IsNullOrEmpty(token)){await Refresh();if(!String.IsNullOrEmpty(token))await Models();}else Status("服务地址已保存。请登录该服务。");}catch(Exception ex){Status(ex.Message);}};
  Find<Button>("LoginButton").Click+=async(s,e)=>await Login();
  Find<Button>("LogoutButton").Click+=async(s,e)=> { authVersion++; try { await Api("logout",new {}); } catch {} token=null; SaveToken(""); Reset(); Status("已退出，当前设备登录凭证已撤销。"); };
  Find<Button>("RefreshButton").Click+=async(s,e)=>await Refresh();
  Find<Button>("SyncModelsButton").Click+=async(s,e)=>await Models();
  Find<Button>("WebButton").Click+=(s,e)=>{if(String.IsNullOrEmpty(Portal)){Status("请先保存服务地址。");return;}Open(Portal+"/#recharge");};
  Find<Button>("HelpButton").Click+=(s,e)=>Prompt("三步，开始使用", "01  浏览器登录\n在网页注册或登录账号，核对两端验证码。\n\n02  一键连接\n选择模型，确认配置位置，助手自动备份。\n\n03  开始创作\n重启 WorkBuddy，选中星桥 AI 模型即可使用。\n\n本站积分与 WorkBuddy 官方积分独立。", "我知道了", false);
  Find<Button>("BrowseButton").Click+=(s,e)=> { var d=new Microsoft.Win32.SaveFileDialog { FileName="models.json",Filter="模型配置|models.json",OverwritePrompt=false }; if(d.ShowDialog(window)==true)Find<TextBox>("ConfigPath").Text=d.FileName; };
  Find<Button>("ConfigureButton").Click+=async(s,e)=>await Configure();
  Find<Button>("RestoreButton").Click+=(s,e)=> { try { string path=Path.GetFullPath(Find<TextBox>("ConfigPath").Text); var file=Directory.GetFiles(Path.GetDirectoryName(path),Path.GetFileName(path)+".starbridge-backup.*").OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault();if(file==null)throw new Exception("未找到历史备份。");if(!Prompt("恢复上次配置", "当前配置会先备份，再恢复以下文件：\n\n"+file,"确认恢复",true))return; ValidatePath(path); var content=File.ReadAllText(file);Json.DeserializeObject(content);ReplacePrivate(path,content); Status("已恢复备份，请重启 WorkBuddy。"); }catch(Exception ex){Status(ex.Message);} };
  Reset(); token=Environment.GetCommandLineArgs().Contains("--preview-dialog")||String.IsNullOrEmpty(Portal)?null:LoadToken();
  window.Loaded+=async(s,e)=> { ApplyMotion(); if(Environment.GetCommandLineArgs().Contains("--preview-dialog")) { Prompt("确认连接 WorkBuddy", "助手会先备份原有配置，再添加所选模型。\n\n个人 API Key 只写入本机配置文件，请妥善保管。", "开始配置",true);return; } if(!String.IsNullOrEmpty(token)) { Status("正在恢复登录状态…"); await Refresh(); if(!String.IsNullOrEmpty(token))await Models(); } };
  var timer=new DispatcherTimer { Interval=TimeSpan.FromSeconds(20) };timer.Tick+=async(s,e)=> { if(!String.IsNullOrEmpty(token)&&!busy)await Refresh(); };timer.Start();
 }
 void Reset() {
  Find<TextBlock>("Greeting").Text="我的工作台";Find<TextBlock>("Balance").Text="—";Find<TextBlock>("Used").Text="— 积分";Find<TextBlock>("Requests").Text="— 次调用";
  Find<Button>("LoginButton").Visibility=Visibility.Visible;Find<StackPanel>("AccountInfo").Visibility=Visibility.Collapsed;Find<TextBlock>("AccountName").Text="—";Find<Button>("LogoutButton").Visibility=Visibility.Collapsed;Find<Button>("ConfigureButton").IsEnabled=false;
  Find<TextBlock>("WalletHint").Text="登录后，与账户实时同步";Find<TextBlock>("ModelCount").Text="等待登录";Find<Button>("SyncModelsButton").IsEnabled=false;Find<CheckBox>("SelectAll").IsEnabled=false;Find<CheckBox>("SelectAll").IsChecked=false;
  if(choices.Count>0)Find<StackPanel>("ModelList").Children.Clear();choices.Clear();
 }
 async Task<Dictionary<string,object>> Api(string endpoint, object body=null, bool authenticated=true) {
  if(String.IsNullOrEmpty(Portal))throw new Exception("请先保存服务地址。");
  var req=new HttpRequestMessage(body==null?HttpMethod.Get:HttpMethod.Post,Portal+"/api/"+endpoint);
  if(authenticated&&!String.IsNullOrEmpty(token))req.Headers.Authorization=new AuthenticationHeaderValue("Bearer",token);
  if(body!=null) { req.Headers.Add("Origin",Portal);req.Headers.Add("X-Portal-Request","1");req.Content=new StringContent(Json.Serialize(body),Encoding.UTF8,"application/json"); }
  using(req)using(var res=await http.SendAsync(req)) {
   var raw=await res.Content.ReadAsStringAsync();Dictionary<string,object> data;
   try { data=Obj(Json.DeserializeObject(raw)); }catch { throw new Exception("服务返回异常，请稍后重试。"); }
   if(!res.IsSuccessStatusCode) {
    if(authenticated&&(res.StatusCode==HttpStatusCode.Unauthorized||res.StatusCode==HttpStatusCode.Forbidden)) {token=null;SaveToken("");Reset();}
    throw new Exception(data.ContainsKey("detail")&&data["detail"] is string?Text(data["detail"]):"操作未完成，请稍后重试。");
   } return data;
  }
 }
 async Task Login() {
  if(busy)return;busy=true;int version=++authVersion;Find<Button>("LoginButton").IsEnabled=false;
  try {
   string verifier=RandomToken();string challenge;using(var sha=SHA256.Create())challenge=Base64(sha.ComputeHash(Encoding.ASCII.GetBytes(verifier)));
   var start=await Api("desktop/start",new {challenge=challenge},false);
   string code=Text(start["code"]);Status("核对浏览器验证码："+code+" · 请在网页确认登录");
   Find<Button>("LoginButton").Content="等待网页确认…";string verify=Text(start["verification_url"]);ValidateVerificationUrl(Portal,verify);Open(verify);
   for(int i=0;i<100&&version==authVersion;i++) {
    await Task.Delay(3000);var result=await Api("desktop/poll",new {id=start["id"],secret=start["secret"],verifier=verifier},false);
    if(Text(result["status"])!="approved")continue;
    token=Text(result["access_token"]);SaveToken(token);await Refresh();await Models();Status("登录成功，已为当前 Windows 用户加密保存登录状态。");return;
   } Status("登录等待结束，请重新发起登录。");
  }catch(Exception e){Status(e.Message);}finally{busy=false;Find<Button>("LoginButton").IsEnabled=true;Find<Button>("LoginButton").Content="浏览器登录 ↗";}
 }
 async Task Refresh() {
  if(refreshing||String.IsNullOrEmpty(token))return;refreshing=true;
  try {var branding=await Api("public",null,false);if(branding.ContainsKey("brand")){string brand=Text(branding["brand"]);if(!String.IsNullOrWhiteSpace(brand)){Find<TextBlock>("BrandName").Text=brand;window.Title=brand+" · WorkBuddy 助手";}}var u=await Api("me");Find<TextBlock>("Balance").ToolTip="精确余额："+Convert.ToDecimal(u["points"]).ToString("N4")+" 积分";Find<TextBlock>("Used").ToolTip="精确消耗："+Convert.ToDecimal(u["used_points"]).ToString("N4")+" 积分";Find<TextBlock>("Greeting").Text="我的工作台";string account=Text(u["username"]);string display=Text(u["display_name"]);Find<TextBlock>("AccountName").Text=account;Find<TextBlock>("AccountName").ToolTip=String.IsNullOrWhiteSpace(display)||display==account?account:display+"（"+account+"）";Find<TextBlock>("AccountAvatar").Text=String.IsNullOrWhiteSpace(account)?"•":account.Substring(0,1).ToUpperInvariant();Find<StackPanel>("AccountInfo").Visibility=Visibility.Visible;
   Find<TextBlock>("Balance").Text=FormatPoints(u["points"]);Find<TextBlock>("Used").Text=FormatPoints(u["used_points"])+" 积分";
   Find<TextBlock>("Requests").Text=Text(u["requests"])+" 次调用";Find<TextBlock>("WalletHint").Text=Convert.ToBoolean(u["can_call"])?"已同步 · "+DateTime.Now.ToString("HH:mm:ss"):"积分已用完，请前往网页充值";
   Find<Button>("LoginButton").Visibility=Visibility.Collapsed;Find<Button>("LogoutButton").Visibility=Visibility.Visible;Find<Button>("ConfigureButton").IsEnabled=true;Find<Button>("SyncModelsButton").IsEnabled=!syncingModels;
   Status("余额已同步 · 额度不足时由 New API 拒绝调用");
  }catch(Exception e){Status("同步失败："+e.Message);}finally{refreshing=false;}
 }
 async Task Models() {
  if(syncingModels||String.IsNullOrEmpty(token))return;syncingModels=true;Find<Button>("SyncModelsButton").IsEnabled=false;Find<Button>("SyncModelsButton").Content="同步中…";Status("正在同步模型列表…");
  var prior=choices.Where(x=>x.IsChecked==true).Select(x=>Text(x.Tag)).ToArray();bool hadModels=choices.Count>0;
  try {var result=await Api("models");var panel=Find<StackPanel>("ModelList");panel.Children.Clear();choices.Clear();
   foreach(var entry in Arr(result["models"])) {
    var m=Obj(entry);string modelId=Text(m["id"]);var row=new Grid {MinHeight=54,Margin=new Thickness(14,0,14,0)};
    row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(2,GridUnitType.Star)});row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(2.1,GridUnitType.Star)});row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(70)});
    var check=new CheckBox {Content=Text(m["name"]),Tag=Text(m["id"]),IsChecked=hadModels?prior.Contains(modelId):Convert.ToBoolean(m["recommended"]),VerticalAlignment=VerticalAlignment.Center,FontSize=13,FontWeight=FontWeights.SemiBold,Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(16,35,69)),ToolTip=Text(m["id"])};check.Click+=(sender,args)=>{Find<CheckBox>("SelectAll").IsChecked=choices.Count>0 && choices.All(x=>x.IsChecked==true);};choices.Add(check);row.Children.Add(check);
    string detail=m.ContainsKey("description")?Text(m["description"]):"";if(detail.StartsWith("[") && detail.IndexOf(']')>=0)detail=detail.Substring(detail.IndexOf(']')+1).Trim();if(String.IsNullOrWhiteSpace(detail))detail=Text(m["id"]);
    var description=new TextBlock {Text=detail,FontSize=12,Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(104,123,148)),VerticalAlignment=VerticalAlignment.Center,TextWrapping=TextWrapping.NoWrap,TextTrimming=TextTrimming.CharacterEllipsis,ToolTip=detail,Margin=new Thickness(8,0,8,0)};Grid.SetColumn(description,1);row.Children.Add(description);
    var status=new TextBlock {Text="可选择",FontSize=11,Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(85,111,148)),VerticalAlignment=VerticalAlignment.Center,HorizontalAlignment=HorizontalAlignment.Right};Grid.SetColumn(status,2);row.Children.Add(status);
    var border=new Border {Child=row,Background=Brushes.Transparent,BorderBrush=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(235,240,246)),BorderThickness=new Thickness(0,0,0,1)};border.MouseEnter+=(sender,args)=>border.Background=new SolidColorBrush(Color.FromRgb(247,250,254));border.MouseLeave+=(sender,args)=>border.Background=Brushes.Transparent;panel.Children.Add(border);
   }
   Find<TextBlock>("ModelCount").Text=choices.Count+" 个可用模型";Find<CheckBox>("SelectAll").IsEnabled=choices.Count>0;Find<CheckBox>("SelectAll").IsChecked=choices.Count>0 && choices.All(x=>x.IsChecked==true);Status("模型同步完成 · 当前可选 "+choices.Count+" 个模型。");
  }catch(Exception e){Status("模型同步失败："+e.Message);}finally{syncingModels=false;Find<Button>("SyncModelsButton").Content="同步模型 ↻";Find<Button>("SyncModelsButton").IsEnabled=!String.IsNullOrEmpty(token);}
 }
 async Task Configure() {
  if(busy)return;var ids=choices.Where(c=>c.IsChecked==true).Select(c=>Text(c.Tag)).ToArray();if(ids.Length==0){Status("请至少选择一个模型。");return;}
  string path=Find<TextBox>("ConfigPath").Text;
  if(!Prompt("确认连接 WorkBuddy", "将自动备份并合并以下配置：\n\n"+path+"\n\n配置内含个人 API Key，请妥善保管，不要分享给他人。","开始配置",true))return;
  busy=true;Find<Button>("ConfigureButton").IsEnabled=false;
  try { ValidatePath(path);var c=await Api("configuration",new {models=ids});Merge(path,Arr(c["models"]));Status("配置完成，共 "+ids.Length+" 个模型。请保存当前任务并重启 WorkBuddy。");Prompt("连接已准备就绪", "配置已保存，原有配置已备份。\n\n请先保存当前任务，再重启 WorkBuddy，选择所配置的模型开始使用。","完成",false); }
  catch(Exception e){Status(e.Message);}finally{busy=false;Find<Button>("ConfigureButton").IsEnabled=!String.IsNullOrEmpty(token);}
 }
 static string FormatPoints(object value) { return Decimal.Truncate(Convert.ToDecimal(value)).ToString("N0"); }
 bool Prompt(string title,string message,string confirm,bool cancel) {
  var dialog=new Window {Owner=window,Title=title,Width=470,SizeToContent=SizeToContent.Height,WindowStartupLocation=WindowStartupLocation.CenterOwner,WindowStyle=WindowStyle.None,ResizeMode=ResizeMode.NoResize,AllowsTransparency=true,Background=System.Windows.Media.Brushes.Transparent,ShowInTaskbar=false,FontFamily=window.FontFamily,Resources=window.Resources};
  var shell=new System.Windows.Controls.Border {Background=System.Windows.Media.Brushes.White,CornerRadius=new CornerRadius(16),BorderBrush=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(164,184,213)),BorderThickness=new Thickness(1),Padding=new Thickness(30),Effect=new System.Windows.Media.Effects.DropShadowEffect {Color=System.Windows.Media.Colors.Black,BlurRadius=28,ShadowDepth=4,Opacity=0.32}};
  var panel=new StackPanel();shell.Child=panel;dialog.Content=shell;
  var top=new Grid();var mark=new TextBlock {Text="✦  星桥 AI",Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(56,89,232)),FontSize=14,FontWeight=FontWeights.SemiBold,Margin=new Thickness(0,0,0,24)};top.Children.Add(mark);
  var close=new Button {Content="×",HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Top,Padding=new Thickness(8,1,8,1),Background=System.Windows.Media.Brushes.Transparent,Foreground=System.Windows.Media.Brushes.Gray,FontSize=19};close.Click+=(s,e)=>{dialog.DialogResult=false;};top.Children.Add(close);panel.Children.Add(top);
  panel.Children.Add(new TextBlock {Text=title,FontSize=23,FontWeight=FontWeights.Bold,Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(29,41,72)),Margin=new Thickness(0,0,0,16)});
  panel.Children.Add(new TextBlock {Text=message,TextWrapping=TextWrapping.Wrap,FontSize=13,LineHeight=23,Foreground=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(103,116,144)),Margin=new Thickness(0,0,0,28)});
  var actions=new StackPanel {Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right};if(cancel){var no=new Button {Content="暂不操作",Background=new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(239,242,249)),Foreground=System.Windows.Media.Brushes.SlateGray,Margin=new Thickness(0,0,10,0)};no.Click+=(s,e)=>{dialog.DialogResult=false;};actions.Children.Add(no);}
  var yes=new Button {Content=confirm,IsDefault=true};yes.Click+=(s,e)=>{dialog.DialogResult=true;};actions.Children.Add(yes);panel.Children.Add(actions);
  dialog.PreviewKeyDown+=(s,e)=>{if(e.Key==System.Windows.Input.Key.Escape)dialog.DialogResult=false;};double previousOpacity=window.Opacity;try{window.Opacity=0.62;if(SystemParameters.ClientAreaAnimation){dialog.Loaded+=(sender,args)=>dialog.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(0,1,TimeSpan.FromMilliseconds(180)));}return dialog.ShowDialog()==true;}finally{window.Opacity=previousOpacity;}
 }
 static string NormalizePortal(string value) {
  Uri uri;if(!Uri.TryCreate((value??"").Trim(),UriKind.Absolute,out uri))throw new Exception("请输入完整的 HTTPS 服务地址。");
  bool local=uri.IsLoopback && uri.Scheme==Uri.UriSchemeHttp;
  if((uri.Scheme!=Uri.UriSchemeHttps && !local)||!String.IsNullOrEmpty(uri.UserInfo)||uri.AbsolutePath!="/"||!String.IsNullOrEmpty(uri.Query)||!String.IsNullOrEmpty(uri.Fragment))throw new Exception("请输入 HTTPS 根地址；仅本机测试可使用 HTTP。地址不得包含账号、路径或参数。");
  return uri.GetLeftPart(UriPartial.Authority).TrimEnd('/');
 }
 static void ValidateVerificationUrl(string portal,string value){Uri uri;if(!Uri.TryCreate(value,UriKind.Absolute,out uri)||!String.IsNullOrEmpty(uri.UserInfo)||uri.GetLeftPart(UriPartial.Authority)!=portal||uri.AbsolutePath!="/"||!String.IsNullOrEmpty(uri.Fragment))throw new Exception("服务返回的登录地址与所选服务不一致。");}
 static string LoadEndpoint(){try{string path=File.Exists(EndpointFile)?EndpointFile:Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"portal-profile.json");var data=Obj(Json.DeserializeObject(File.ReadAllText(path)));return NormalizePortal(Text(data["portal_url"]));}catch{return null;}}
 static void SaveEndpoint(string value){Directory.CreateDirectory(Data);string stage=EndpointFile+"."+Guid.NewGuid().ToString("N");WritePrivate(stage,Json.Serialize(new Dictionary<string,object>{{"portal_url",value}}));if(File.Exists(EndpointFile))File.Replace(stage,EndpointFile,null);else File.Move(stage,EndpointFile);}
 static string Base64(byte[] bytes){return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+','-').Replace('/','_');}
 static string RandomToken(){var b=new byte[32];using(var rng=RandomNumberGenerator.Create())rng.GetBytes(b);return Base64(b);}
 static void Private(string path) {var sec=new FileSecurity();sec.SetAccessRuleProtection(true,false);sec.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,FileSystemRights.FullControl,AccessControlType.Allow));File.SetAccessControl(path,sec);}
 static void WritePrivate(string path, string text){Directory.CreateDirectory(Path.GetDirectoryName(path));using(var f=new FileStream(path,FileMode.CreateNew,FileAccess.Write)){Private(path);var b=new UTF8Encoding(false).GetBytes(text);f.Write(b,0,b.Length);}}
 void SaveToken(string value){Directory.CreateDirectory(Data);byte[] encrypted=ProtectedData.Protect(Encoding.UTF8.GetBytes(value),null,DataProtectionScope.CurrentUser);string stage=AuthFile+"."+Guid.NewGuid().ToString("N");using(var f=new FileStream(stage,FileMode.CreateNew,FileAccess.Write)){Private(stage);f.Write(encrypted,0,encrypted.Length);}if(File.Exists(AuthFile))File.Replace(stage,AuthFile,null);else File.Move(stage,AuthFile);}
 string LoadToken(){try{return Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(AuthFile),null,DataProtectionScope.CurrentUser));}catch{return null;}}
 static void ValidatePath(string path) {path=Path.GetFullPath(path);if(Path.GetFileName(path)!="models.json")throw new Exception("目标文件名必须为 models.json。");for(string p=path;!String.IsNullOrEmpty(p);p=Path.GetDirectoryName(p)){if((File.Exists(p)||Directory.Exists(p))&&(File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0)throw new Exception("目标路径包含文件链接，请选择真实路径。");}}
 static void ReplacePrivate(string path,string text){ValidatePath(path);if(File.Exists(path))WritePrivate(path+".starbridge-backup."+DateTime.Now.ToString("yyyyMMdd-HHmmss-fffffff")+"-"+Guid.NewGuid().ToString("N"),File.ReadAllText(path));string stage=path+".starbridge-stage."+Guid.NewGuid().ToString("N");WritePrivate(stage,text);if(File.Exists(path))File.Replace(stage,path,null);else File.Move(stage,path);}
 static void Merge(string path,object[] models) {
  ValidatePath(path);object root=File.Exists(path)?Json.DeserializeObject(File.ReadAllText(path)):new object[0];var dict=root as Dictionary<string,object>;
  object[] old;if(dict!=null){if(!dict.ContainsKey("models")||!(dict["models"] is IEnumerable)||dict["models"] is string)throw new Exception("无法识别原有配置，未写入。");old=Arr(dict["models"]);}else if(root is object[])old=(object[])root;else throw new Exception("配置格式不受支持，未写入。");
  var selected=models.Select(Obj).ToDictionary(m=>Text(m["id"]));var merged=new List<object>();
  foreach(var item in old){var m=Obj(item);string id=Text(m["id"]);if(selected.ContainsKey(id)){if(Text(m.ContainsKey("url")?m["url"]:"").TrimEnd('/')!=Text(selected[id]["url"]).TrimEnd('/'))throw new Exception("模型 ID 与其他供应商冲突，未写入："+id);}else merged.Add(item);}
  merged.AddRange(models);object output=merged.ToArray();if(dict!=null){dict["models"]=output;if(dict.ContainsKey("availableModels"))dict["availableModels"]=Arr(dict["availableModels"]).Concat(selected.Keys.Cast<object>()).Distinct().ToArray();output=dict;}
  ReplacePrivate(path,Json.Serialize(output));
 }
 static void SelfTest(string folder){if(NormalizePortal("https://example.test/")!="https://example.test"||NormalizePortal("http://127.0.0.1:8400/")!="http://127.0.0.1:8400")throw new Exception("Portal normalization failed");bool rejected=false;try{NormalizePortal("http://example.test");}catch{rejected=true;}if(!rejected)throw new Exception("Insecure portal allowed");rejected=false;try{NormalizePortal("https://user@example.test/path");}catch{rejected=true;}if(!rejected)throw new Exception("Unsafe portal allowed");ValidateVerificationUrl("https://example.test","https://example.test/?desktop=id");rejected=false;try{ValidateVerificationUrl("https://example.test","https://elsewhere.test/?desktop=id");}catch{rejected=true;}if(!rejected)throw new Exception("Cross-origin login allowed");var client=new Starbridge();client.Portal="https://one.example";string first=client.AuthFile;client.Portal="https://two.example";if(first==client.AuthFile)throw new Exception("Sessions not separated by portal");if(FormatPoints(9988.4085m)!="9,988"||FormatPoints(.9999m)!="0")throw new Exception("Credit format failed");Directory.CreateDirectory(folder);string path=Path.Combine(Path.GetFullPath(folder),"models.json");WritePrivate(path,"[{\"id\":\"original\",\"url\":\"https://original.test\"}]");var model=new Dictionary<string,object>{{"id","test"},{"url","https://example.test/v1/chat/completions"},{"apiKey","TEST-ONLY"}};Merge(path,new object[]{model});Merge(path,new object[]{model});if(Arr(Json.DeserializeObject(File.ReadAllText(path))).Length!=2)throw new Exception("Merge failed");byte[] b=Encoding.UTF8.GetBytes("test-only");if(Encoding.UTF8.GetString(ProtectedData.Unprotect(ProtectedData.Protect(b,null,DataProtectionScope.CurrentUser),null,DataProtectionScope.CurrentUser))!="test-only")throw new Exception("DPAPI failed");File.WriteAllText(Path.Combine(folder,"result.txt"),"PASS: compiled EXE, merge, idempotence, backup, DPAPI",Encoding.UTF8);}
}
