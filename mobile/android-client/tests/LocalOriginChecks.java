import com.zhenxing.agent.local.LocalOrigin;

/** Host-policy tests without Android runtime or network calls. */
public final class LocalOriginChecks {
 private static int passed;
 private static void yes(boolean value,String label){if(!value)throw new AssertionError(label);passed++;}
 private static void allowed(String raw,String expected){yes(LocalOrigin.parse(raw).equals(expected),"allowed: "+raw);}
 private static void denied(String raw){
  boolean rejected=false;try{LocalOrigin.parse(raw);}catch(IllegalArgumentException expected){rejected=true;}
  yes(rejected,"must reject: "+raw);
 }
 public static void main(String[] args){
  allowed("192.168.31.42:18769","http://192.168.31.42:18769");
  allowed("  http://192.168.31.42:18769/#/workspace  ","http://192.168.31.42:18769");
  allowed("https://10.0.2.2/ui/","https://10.0.2.2");
  allowed("HTTP://LOCALHOST:18765/","http://localhost:18765");
  allowed("http://127.0.0.1:18765/","http://127.0.0.1:18765");
  allowed("http://172.16.0.1:80","http://172.16.0.1:80");
  allowed("http://172.31.255.254:65535","http://172.31.255.254:65535");
  for(String raw:new String[]{null,"","https://example.com","https://8.8.8.8","http://172.15.255.255",
    "http://172.32.0.1","http://192.169.0.1","http://169.254.1.1","http://0.0.0.0","file:///etc/passwd",
    "javascript:alert(1)","content://private/data","http://user:secret@192.168.1.2","http://192.168.1.2@8.8.8.8",
    "http://192.168.1.2/?redirect=https://example.com","http://192.168.1.2/?","http://192.168.1.2/admin",
    "http://192.168.1.2/ui/../../api","http://192.168.1.2:0","http://192.168.1.2:65536","http://192.168.1.2:-1",
    "http://2130706433","http://0177.0.0.1","http://127.1","http://0x7f.0.0.1","http://192.168.01.2",
    "http://192.168.256.2","http://192.168.1.2.","http://192.168.1.2\\@8.8.8.8","http://[::1]",
    "http://[::ffff:127.0.0.1]","http://192.168.1.2\n:80"})denied(raw);
  String origin="http://192.168.31.42:18769";
  yes(LocalOrigin.same(origin,origin+"/api/v3/conversations?limit=2"),"same-origin API paths");
  yes(LocalOrigin.same(origin,origin+"/#/workspace"),"same-origin hash route");
  yes(LocalOrigin.same("http://localhost","http://LOCALHOST:80/ui/"),"HTTP default port");
  yes(LocalOrigin.same("https://localhost","https://localhost:443/ui/"),"HTTPS default port");
  for(String candidate:new String[]{"https://192.168.31.42:18769/api","http://192.168.31.42:18765/api",
    "http://192.168.31.43:18769/api","http://192.168.31.42.example.com:18769/api",
    "http://user@192.168.31.42:18769/api","http://192.168.31.42:18769@8.8.8.8/api",
    "file:///storage/data","content://private/data","data:text/html,hello","javascript:alert(1)",null})
   yes(!LocalOrigin.same(origin,candidate),"cross-origin reject: "+candidate);
  // A legitimate case-folded URL still uses the HTTPS default port.
  boolean upperCaseHttps=LocalOrigin.same("https://localhost","HTTPS://LOCALHOST/ui/");
  yes(upperCaseHttps,"HTTPS case-folded default port");
  System.out.println("{\"tests\":"+passed+",\"failures\":0,\"uppercase_https_default_port\":"+upperCaseHttps+"}");
 }
}
