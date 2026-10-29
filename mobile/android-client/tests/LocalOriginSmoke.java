import com.zhenxing.agent.local.LocalOrigin;
public class LocalOriginSmoke {
 public static void main(String[] args) {
  if(!LocalOrigin.same("https://localhost","HTTPS://LOCALHOST/ui/"))throw new AssertionError("default port case");
  System.out.println("DEFAULT_PORT_SCHEME_CASE_OK");
 }
}
