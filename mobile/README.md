# 圳行Agent移动端

## 目录

- [安装与连接](连接说明.md)
- [源码构建](BUILDING.md)
- [技术结构](../docs/移动端技术说明.md)
- [测试与版本](../docs/移动端验证说明.md)
- [安装包与校验值](dist/SHA256SUMS.txt)

本目录提供Android客户端、内置网页源码、局域网连接工具及0.1.1 APK。界面由APK内置资源加载，账户与业务由电脑端同一套API处理。

先按根目录部署说明启动app中的电脑服务，再安装dist中的APK。移动设备要求Android 9及以上，Android System WebView或Chrome 120及以上。

```text
mobile/
  android-client/   Java入口、资源、兼容脚本、网关和测试
  web/              APK对应的React/TypeScript网页源码和锁文件
  dist/             0.1.1 APK、构建信息和SHA-256
  Build-Android.ps1 构建入口
  Start-ComputerService.ps1 局域网连接入口
```

移动端使用独立的前端快照。修改电脑网页不会自动更新APK中的界面；更新移动界面时需要重新构建与安装。服务端的会话、任务和账务规则共用。
