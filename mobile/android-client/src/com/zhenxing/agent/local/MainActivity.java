package com.zhenxing.agent.local;

import android.app.*;
import android.os.*;
import android.content.*;
import android.content.pm.PackageInfo;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.provider.OpenableColumns;
import android.view.*;
import android.webkit.*;
import android.webkit.CookieManager;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import org.json.JSONObject;

public class MainActivity extends Activity {
 private WebView web;
 private LinearLayout root;
 private ProgressBar progress;
 private EditText address;
 private TextView notice;
 private String origin;
 private volatile int connectionEpoch=0;
 private final ExecutorService io=Executors.newSingleThreadExecutor();
 private ValueCallback<Uri[]> chooser;
 private ValueCallback<Uri[]> pendingFileCallback;
 private Uri[] pendingFiles;
 private boolean resumed=false,deliveryScheduled=false;
 private final Set<String> selectedContentUris=ConcurrentHashMap.newKeySet();
 private int fileEpoch=0;
 private String chooserOrigin;
 private static final int PICK_IMAGES=40;
 private static final String COMPAT="/__zx_android__/compat.js";
 private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
 private TextView text(String s,int size){TextView v=new TextView(this);v.setText(s);v.setTextSize(size);v.setTextColor(Color.rgb(25,51,68));v.setPadding(0,dp(8),0,dp(8));return v;}
 private Button button(String s){Button b=new Button(this);b.setText(s);b.setAllCaps(false);return b;}
 @Override public void onCreate(Bundle saved) {
  super.onCreate(saved);
  getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
  WebView.setWebContentsDebuggingEnabled(false);
  home();
 }
 private void home() {
  connectionEpoch++;
  fileEpoch++;deliveryScheduled=false;selectedContentUris.clear();
  if(chooser!=null){chooser.onReceiveValue(null);chooser=null;}chooserOrigin=null;
  if(pendingFileCallback!=null){pendingFileCallback.onReceiveValue(null);pendingFileCallback=null;}pendingFiles=null;
  if(web!=null){web.stopLoading();web.destroy();web=null;}
  root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(dp(24),dp(42),dp(24),dp(24));root.setBackgroundColor(Color.rgb(245,249,248));
  ScrollView setupScroll=new ScrollView(this);setupScroll.setFillViewport(true);setupScroll.addView(root);setContentView(setupScroll);
  ImageView logo=new ImageView(this);logo.setImageResource(R.drawable.ic_launcher);root.addView(logo,new LinearLayout.LayoutParams(dp(64),dp(64)));
  root.addView(text("圳行Agent",28));root.addView(text("连接电脑上的服务",20));
  root.addView(text("手机与电脑连接同一个网络。填写电脑端启动工具显示的地址。",16));
  address=new EditText(this);address.setSingleLine(true);address.setTextSize(16);address.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_URI);
  address.setHint("http://192.168.1.10:18769");address.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_DONE);address.setText(getPreferences(0).getString("server", ""));root.addView(address,new LinearLayout.LayoutParams(-1,dp(60)));
  Button connect=button("连接服务");root.addView(connect,new LinearLayout.LayoutParams(-1,dp(54)));
  notice=text("",14);root.addView(notice);
  root.addView(text("登录、查询和办理使用电脑端的现有账户。电脑服务需保持运行。",14));
  connect.setOnClickListener(v->{
   try {
    PackageInfo info=WebView.getCurrentWebViewPackage();
    if(info!=null && Integer.parseInt(info.versionName.split("\\.")[0])<120){notice.setText("请先更新 Android System WebView 或 Chrome，再连接服务。");return;}
    connect(LocalOrigin.parse(address.getText().toString()),connect);
   }catch(IllegalArgumentException e){notice.setText(e.getMessage());}
  });
  address.setOnEditorActionListener((v,action,event)->{if(action==android.view.inputmethod.EditorInfo.IME_ACTION_DONE){connect.performClick();return true;}return false;});
 }
 private void connect(String server,Button button) {
  ((android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(address.getWindowToken(),0);
  button.setEnabled(false);notice.setText("正在连接电脑服务…");int epoch=++connectionEpoch;
  io.execute(()->{
   String error=null;
   try {
    HttpURLConnection c=(HttpURLConnection)new URL(server+"/api/v2/health/ready").openConnection();
    c.setConnectTimeout(6000);c.setReadTimeout(8000);c.setInstanceFollowRedirects(false);
    try {
     if(c.getResponseCode()!=200 || !"ready".equals(new JSONObject(new String(read(c.getInputStream(),16384),StandardCharsets.UTF_8)).optString("status")))
       error="电脑服务尚未就绪，请先运行电脑端启动工具。";
    }finally{c.disconnect();}
   }catch(Exception e){error="未连接到电脑。请检查地址、同一网络以及电脑防火墙设置。";}
   final String message=error;
   runOnUiThread(()->{
    if(epoch!=connectionEpoch || isFinishing())return;
    button.setEnabled(true);
    if(message!=null){notice.setText(message);return;}
    String old=getPreferences(0).getString("server","");
    origin=server;getPreferences(0).edit().putString("server",server).apply();
    if(!old.equals(server)) {
     WebStorage.getInstance().deleteAllData();
     CookieManager.getInstance().removeAllCookies(done->{if(epoch==connectionEpoch&&!isFinishing())showWeb();});
    } else showWeb();
   });
  });
 }
 private void showWeb() {
  root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.WHITE);setContentView(root);
  progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);progress.setMax(100);root.addView(progress,new LinearLayout.LayoutParams(-1,dp(2)));
  web=new WebView(this);root.addView(web,new LinearLayout.LayoutParams(-1,0,1));
  WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(true);
  s.setAllowFileAccessFromFileURLs(false);s.setAllowUniversalAccessFromFileURLs(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
  s.setSupportMultipleWindows(false);s.setJavaScriptCanOpenWindowsAutomatically(false);s.setBuiltInZoomControls(true);s.setDisplayZoomControls(false);s.setTextZoom(100);
  s.setUseWideViewPort(true);s.setLoadWithOverviewMode(true);s.setCacheMode(WebSettings.LOAD_DEFAULT);
  CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
  web.setWebViewClient(new WebViewClient(){
   @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
    if(LocalOrigin.same(origin,request.getUrl().toString()))return false;
    if(request.isForMainFrame()&&request.hasGesture()&&"https".equals(request.getUrl().getScheme())){
     try{startActivity(new Intent(Intent.ACTION_VIEW,request.getUrl()));}catch(ActivityNotFoundException e){Toast.makeText(MainActivity.this,"未找到浏览器",Toast.LENGTH_SHORT).show();}
    }
    return true;
   }
   @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
    String url=request.getUrl().toString(),path=request.getUrl().getPath();
    if("content".equals(request.getUrl().getScheme())&&selectedContentUris.contains(url))return null;
    if(!LocalOrigin.same(origin,url))return blocked();
    if(COMPAT.equals(path))try{return new WebResourceResponse("application/javascript","UTF-8",getAssets().open("compat.js"));}catch(IOException e){return blocked();}
    if("/__zx_android__/mobile.css".equals(path))try{return new WebResourceResponse("text/css","UTF-8",getAssets().open("mobile.css"));}catch(IOException e){return blocked();}
    if(request.isForMainFrame()&&request.getMethod().equals("GET")&&(path.equals("/")||path.equals("/ui/")||path.equals("/ui/index.html"))) {
     try {
       String html=new String(read(getAssets().open("web/index.html"),2*1024*1024),StandardCharsets.UTF_8);
       if(!html.contains("<head>"))throw new IOException();
       html=html.replace("<head>","<head><script src=\""+COMPAT+"\"></script>");
       html=html.replace("</head>","<link rel=\"stylesheet\" href=\"/__zx_android__/mobile.css?v=2\"></head>");
       int viewportWidth=Math.round(getResources().getConfiguration().screenWidthDp/0.86f);
       html=html.replaceFirst("(?i)<meta\\s+name=\"viewport\"[^>]*>","<meta name=\"viewport\" content=\"width="+viewportWidth+", initial-scale=0.86, minimum-scale=0.65, maximum-scale=2, user-scalable=yes\">");
       Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");
       return new WebResourceResponse("text/html","UTF-8",200,"OK",headers,new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));
     }catch(Exception e){return new WebResourceResponse("text/html","UTF-8",503,"Unavailable",Collections.emptyMap(),new ByteArrayInputStream("<meta charset='utf-8'><p>页面暂时不可用，请从屏幕右侧边缘向左滑，检查电脑连接。</p>".getBytes(StandardCharsets.UTF_8)));}
    }
    if(path.startsWith("/ui/")&&path.matches("/ui/[A-Za-z0-9_./-]+")&&!path.contains("..")) {
     String mime=path.endsWith(".js")?"application/javascript":path.endsWith(".css")?"text/css":path.endsWith(".svg")?"image/svg+xml":path.endsWith(".png")?"image/png":path.endsWith(".json")?"application/json":"application/octet-stream";
     try{return new WebResourceResponse(mime,"UTF-8",getAssets().open("web/"+path.substring(4)));}catch(IOException e){return blocked();}
    }
    return null;
   }
   @Override public void onReceivedSslError(WebView view,SslErrorHandler handler,SslError error){handler.cancel();}
   @Override public void onReceivedError(WebView view,WebResourceRequest r,WebResourceError e){if(r.isForMainFrame())Toast.makeText(MainActivity.this,"连接中断，请通过连接设置检查电脑服务",Toast.LENGTH_LONG).show();}
  });
  web.setWebChromeClient(new WebChromeClient(){
   @Override public void onProgressChanged(WebView view,int n){progress.setProgress(n);progress.setVisibility(n==100?View.GONE:View.VISIBLE);}
   @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
    if(!LocalOrigin.same(origin,view.getUrl())){callback.onReceiveValue(null);return true;}
    if(pendingFileCallback!=null)finishFileDelivery(false);
    fileEpoch++;deliveryScheduled=false;
    if(chooser!=null)chooser.onReceiveValue(null);chooser=callback;chooserOrigin=origin;
    Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT);pick.addCategory(Intent.CATEGORY_OPENABLE);pick.setType("image/*");pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true);
    pick.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
    try{startActivityForResult(pick,PICK_IMAGES);}catch(ActivityNotFoundException e){chooser.onReceiveValue(null);chooser=null;}
    return true;
   }
  });
  final float[] edgeStart=new float[2];final boolean[] edgeActive={false};
  web.setOnTouchListener((v,event)->{
   if(event.getPointerCount()>1){edgeActive[0]=false;return false;}
   if(event.getActionMasked()==MotionEvent.ACTION_DOWN){edgeStart[0]=event.getX();edgeStart[1]=event.getY();edgeActive[0]=event.getX()>web.getWidth()-dp(24);}
   else if(event.getActionMasked()==MotionEvent.ACTION_MOVE&&edgeActive[0]&&edgeStart[0]-event.getX()>dp(80)&&Math.abs(event.getY()-edgeStart[1])<dp(50)){
    edgeActive[0]=false;MotionEvent cancel=MotionEvent.obtain(event);cancel.setAction(MotionEvent.ACTION_CANCEL);web.onTouchEvent(cancel);cancel.recycle();connectionSettings();return true;
   }else if(event.getActionMasked()==MotionEvent.ACTION_UP||event.getActionMasked()==MotionEvent.ACTION_CANCEL)edgeActive[0]=false;
   return false;
  });
  web.loadUrl(origin+"/#/workspace");
 }
 private void connectionSettings(){new AlertDialog.Builder(this).setTitle("电脑连接").setMessage(origin+"\n更换连接会重新打开网页。")
   .setNegativeButton("继续使用",null).setPositiveButton("更换地址",(d,w)->home()).show();}
 private static WebResourceResponse blocked(){return new WebResourceResponse("text/plain","UTF-8",403,"Blocked",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));}
 private static byte[] read(InputStream in,int limit)throws IOException{
  try(InputStream stream=in;ByteArrayOutputStream out=new ByteArrayOutputStream()){
   byte[] buffer=new byte[8192];int n;while((n=stream.read(buffer))!=-1){if(out.size()+n>limit)throw new IOException("Too large");out.write(buffer,0,n);}return out.toByteArray();
  }
 }
 @Override protected void onActivityResult(int request,int result,Intent data){
  super.onActivityResult(request,result,data);if(request!=PICK_IMAGES||chooser==null)return;
  ValueCallback<Uri[]> callback=chooser;chooser=null;List<Uri> selected=new ArrayList<>();
  if(result==RESULT_OK && data!=null && web!=null && origin!=null&&origin.equals(chooserOrigin)
     && LocalOrigin.same(origin,web.getUrl())) {
   if(data.getClipData()!=null)for(int i=0;i<data.getClipData().getItemCount();i++)selected.add(data.getClipData().getItemAt(i).getUri());
   else if(data.getData()!=null)selected.add(data.getData());
  }
  boolean valid=selected.size()<=5;
  for(Uri uri:selected){
   try {
    String mime=getContentResolver().getType(uri);
    if(!"content".equals(uri.getScheme())||mime==null||!Arrays.asList("image/jpeg","image/png","image/webp").contains(mime)){valid=false;break;}
    try(Cursor cursor=getContentResolver().query(uri,new String[]{OpenableColumns.SIZE},null,null,null)){
     if(cursor!=null&&cursor.moveToFirst()&&cursor.getLong(0)>5L*1024*1024){valid=false;break;}
    }
   }catch(Exception e){valid=false;break;}
  }
  if(!valid){Toast.makeText(this,"最多选择5张PNG、JPEG或WebP图片，每张不超过5MB",Toast.LENGTH_LONG).show();selected.clear();}
  selectedContentUris.clear();for(Uri uri:selected)selectedContentUris.add(uri.toString());
  pendingFileCallback=callback;pendingFiles=selected.isEmpty()?null:selected.toArray(new Uri[0]);
  if(pendingFiles==null)finishFileDelivery(false);else publishFilesWhenReady(0);
 }
 private void publishFilesWhenReady(int attempt){
  if(pendingFileCallback==null||deliveryScheduled||!resumed||!hasWindowFocus()||web==null)return;
  if(!LocalOrigin.same(origin,web.getUrl())){finishFileDelivery(false);return;}
  deliveryScheduled=true;final int epoch=fileEpoch;final WebView owner=web;final ValueCallback<Uri[]> expected=pendingFileCallback;
  owner.postDelayed(()->{
   if(epoch!=fileEpoch||web!=owner||pendingFileCallback!=expected)return;
   if(!resumed||!hasWindowFocus()){deliveryScheduled=false;return;}
   // Android returns picker results before the web page regains visibility.
   // Wait for the existing page authorization gate; never override its checks.
   owner.evaluateJavascript("(()=>{const f=document.querySelector('input[type=file][multiple]');const b=f?.nextElementSibling;return document.visibilityState==='visible'&&b instanceof HTMLButtonElement&&!b.disabled&&b.getAttribute('aria-disabled')!=='true'})()",ready->{
    if(epoch!=fileEpoch||web!=owner||pendingFileCallback!=expected)return;
    deliveryScheduled=false;
    if(!resumed||!hasWindowFocus())return;
    if(!LocalOrigin.same(origin,owner.getUrl())){finishFileDelivery(false);return;}
    if("true".equals(ready)){finishFileDelivery(true);return;}
    if(attempt<40)publishFilesWhenReady(attempt+1);
    else {Toast.makeText(this,"图片选择尚未就绪，请再试一次",Toast.LENGTH_LONG).show();finishFileDelivery(false);}
   });
  },100);
 }
 private void finishFileDelivery(boolean valid){
  ValueCallback<Uri[]> callback=pendingFileCallback;Uri[] files=valid?pendingFiles:null;pendingFileCallback=null;pendingFiles=null;
  if(callback!=null)callback.onReceiveValue(files);
 }
 @Override public void onBackPressed(){
  if(web!=null&&web.canGoBack()){web.goBack();return;}
  new AlertDialog.Builder(this).setMessage("退出圳行Agent？").setNegativeButton("继续使用",null).setNeutralButton("连接设置",(d,w)->connectionSettings()).setPositiveButton("退出",(d,w)->finish()).show();
 }
 @Override protected void onPause(){resumed=false;super.onPause();if(web!=null)web.onPause();}
 @Override protected void onResume(){super.onResume();resumed=true;if(web!=null)web.onResume();publishFilesWhenReady(0);}
 @Override public void onWindowFocusChanged(boolean focused){super.onWindowFocusChanged(focused);if(focused)publishFilesWhenReady(0);}
 @Override protected void onDestroy(){connectionEpoch++;if(chooser!=null){chooser.onReceiveValue(null);chooser=null;}finishFileDelivery(false);if(web!=null)web.destroy();io.shutdownNow();super.onDestroy();}
}
