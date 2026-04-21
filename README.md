<p align="center">
<pre align="center">
██╗  ██╗     ██╗██████╗
╚██╗██╔╝     ██║██╔══██╗
 ╚███╔╝      ██║██║  ██║
 ██╔██╗ ██   ██║██║  ██║
██╔╝ ██╗╚█████╔╝██████╔╝
╚═╝  ╚═╝ ╚════╝ ╚═════╝
    X J D  H u b
 Skill Marketplace
</pre>
</p>

<p align="center">
  Skill marketplace for <a href="https://github.com/allinxjd/xjd-agent">XJD-Agent</a> — publish, discover, and install community skills.<br>
  XJD-Agent 技能市场 — 发布、搜索、安装社区技能
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/version-0.1.0-green.svg" alt="Version">
</p>

---

## 功能

- 技能上传 / 下载 / 搜索 API
- Ed25519 技能包签名验证
- 积分系统 + License 管理
- 用户认证与评价系统

## 快速开始

```bash
pip install -e .
xjd-hub serve --port 8090
```

## 架构

```
xjd-hub/
├── server.py          # FastAPI 服务入口
├── db.py              # SQLite 数据层
├── models.py          # 数据模型
├── signing.py         # Ed25519 签名验证
├── routes/
│   ├── auth.py        # 认证路由
│   ├── skills.py      # 技能管理路由
│   └── reviews.py     # 评价系统路由
└── payment/
    ├── credits.py     # 积分系统
    └── license.py     # License 管理
```

## 关联项目

- [xjd-agent](https://github.com/allinxjd/xjd-agent) — AI Agent 核心平台

## License

MIT
