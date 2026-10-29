# 工程目录

| 路径 | 内容 |
|---|---|
| app/src/zhenxing | API、语义、知识、DAG、业务工具与数据初始化 |
| app/web | React / TypeScript网页及行为测试 |
| app/tests | 审核选集与所需测试辅助模块 |
| app/compose.yaml | 六个常驻服务及一次性init-data服务 |
| mobile | Android源码、0.1.1 APK、构建与电脑连接工具 |
| docs | 部署、示例数据、接口、测试和安全边界说明 |
| evidence | 脱敏后的评测与本次交付检查摘要 |
| licenses | 实际内置运行依赖的许可证副本 |

应用数据由release_seed在全新数据库中生成。SDK、模型权重、运行数据库、密钥和开发会话不随源码分发。
