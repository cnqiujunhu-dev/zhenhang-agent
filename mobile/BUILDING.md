# 移动端构建

## 环境

Windows、Python 3.11及以上、Node.js 24、JDK 21。首次构建需联网下载锁定的npm依赖和Android官方构建组件。建议在非系统盘的英文路径中解压，例如D:\ZhenxingSubmission。

在mobile目录运行：

```powershell
.\Build-Android.ps1
```

脚本在web中执行npm ci和Vite构建，将HTML、JavaScript、CSS与图标放入android-client/assets/web。兼容脚本使用固定版本@noble/hashes和rolldown。原生部分依次使用aapt2、javac、D8、zipalign和apksigner；无需安装Android Studio或Gradle。

SDK、缓存和临时文件写入所选工程的var目录。构建产物写入dist。可用-CheckOnly检查已准备的环境；可用-Python指定解释器。

## 签名与复建

自行构建时，脚本在var/private/android-signing生成本地签名。提交材料不提供原发行签名私钥。新签名构建不能直接覆盖原签名安装，二进制SHA-256也会不同；这里提供的是可构建源码与发行APK校验值，不承诺重新生成相同签名的逐字节文件。

## 离线检查

```powershell
python android-client/tests/run_offline_checks.py
python -m pytest android-client/tests/test_lan_gateway.py -q
```

第一条编译并执行来源地址策略检查、兼容脚本的哈希/UUID和视口检查；第二条检查网关地址、路径、请求头、体积与断连处理。它们不访问业务数据库或云模型。
