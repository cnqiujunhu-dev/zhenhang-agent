package com.zhenxing.agent.local;

import java.net.URI;
import java.util.Locale;

/** Explicit private IPv4/loopback origin; never an arbitrary public browser. */
public final class LocalOrigin {
 public static String parse(String raw) {
  if (raw == null || raw.trim().length() > 200) throw new IllegalArgumentException("请输入电脑服务地址");
  String value = raw.trim();
  if (!value.contains("://")) value = "http://" + value;
  try {
   URI uri = new URI(value);
   String scheme = uri.getScheme().toLowerCase(Locale.ROOT), host = uri.getHost();
   if (!(scheme.equals("http") || scheme.equals("https")) || host == null || uri.getRawUserInfo() != null
      || uri.getRawQuery() != null || !privateHost(host) || uri.getPort() == 0 || uri.getPort() > 65535
      || !(uri.getPath().isEmpty() || uri.getPath().equals("/") || uri.getPath().equals("/ui/"))) throw new Exception();
   return scheme + "://" + host.toLowerCase(Locale.ROOT) + (uri.getPort() == -1 ? "" : ":" + uri.getPort());
  } catch (Exception ex) { throw new IllegalArgumentException("请填写局域网IP和端口，例如 http://192.168.1.10:18769"); }
 }
 public static boolean privateHost(String host) {
  if (host.equalsIgnoreCase("localhost")) return true;
  String[] bits = host.split("\\.", -1); if (bits.length != 4) return false;
  int[] a = new int[4];
  for (int i=0;i<4;i++) {
   if (!bits[i].matches("0|[1-9][0-9]{0,2}")) return false;
   a[i] = Integer.parseInt(bits[i]); if(a[i]>255)return false;
  }
  return a[0]==10 || a[0]==127 || a[0]==192&&a[1]==168 || a[0]==172&&a[1]>=16&&a[1]<=31;
 }
 public static boolean same(String origin,String candidate) {
  try {
   URI a=new URI(origin),b=new URI(candidate);
   return a.getScheme().equalsIgnoreCase(b.getScheme()) && a.getHost().equalsIgnoreCase(b.getHost())
     && port(a)==port(b) && b.getRawUserInfo()==null;
  }catch(Exception e){return false;}
 }
 private static int port(URI u){return u.getPort()!=-1?u.getPort():"https".equalsIgnoreCase(u.getScheme())?443:80;}
}
