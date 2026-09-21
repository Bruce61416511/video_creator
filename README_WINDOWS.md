# 万相视频生成工具 - Windows 部署指南

## 项目简介

调用阿里云 DashScope API 的 AI 视频生成工具，支持：
- 文生视频 / 图生视频
- 提示词优化
- 脚本分镜拆分
- TTS 配音生成

---

## 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.10+ | 推荐 3.11 或 3.12 |
| Node.js | 18+ | 推荐 20 LTS |
| 内存 | ≥ 4GB | 开发模式 |
| 硬盘 | ≥ 500MB | 代码 + 依赖 |

---

## 快速开始

### 1. 克隆/下载项目

```bash
# 如果从 git 仓库
git clone <repo-url>
cd 视频生成

# 或直接解压下载的文件
cd 视频生成
```

### 2. 部署后端

```powershell
# 进入后端目录
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\Activate.ps1

# 如果提示执行策略错误，先运行：
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
copy .env.example .env
# 编辑 .env，填入你的 DashScope API Key

# 启动后端（默认端口 8002）
python main.py
```

后端启动后会看到：
```
INFO:     Uvicorn running on http://0.0.0.0:8002
```

### 3. 部署前端

**新开一个终端窗口**：

```powershell
# 进入前端目录
cd frontend

# 安装依赖
npm install

# 启动开发服务器（默认端口 5175）
npm run dev
```

前端启动后会看到：
```
  VITE v6.x.x  ready in xxx ms

  ➜  Local:   http://localhost:5175/
```

### 4. 访问

打开浏览器访问：**http://localhost:5175**

---

## 配置说明

### 后端 .env 文件

```bash
# DashScope API Key（视频生成 / TTS）
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# LLM API Key（提示词优化 / 脚本拆分）
# 留空则复用 DASHSCOPE_API_KEY
LLM_API_KEY=
```

**获取 API Key**：
1. 登录 [阿里云百炼控制台](https://bailian.console.aliyun.com/)
2. 进入「API-KEY 管理」
3. 创建新的 API Key

### 端口配置

如果端口冲突，需要修改两个地方：

**后端端口**（`backend/main.py` 最后一行）：
```python
uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)  # 改成你想要的端口
```

**前端代理**（`frontend/vite.config.js`）：
```javascript
proxy: {
  '/api': 'http://localhost:8003',      // 改成和后端一致的端口
  '/uploads': 'http://localhost:8003',
}
```

---

## 生产部署（可选）

### 后端生产模式

```powershell
cd backend
.\venv\Scripts\Activate.ps1

# 使用 uvicorn 直接启动（推荐）
uvicorn main:app --host 0.0.0.0 --port 8002 --workers 2
```

### 前端构建

```powershell
cd frontend

# 构建生产版本
npm run build

# 构建产物在 dist/ 目录
# 可以用 nginx 或其他静态服务器托管
```

---

## 常见问题

### Q: pip install 报错
```
# 尝试升级 pip
python -m pip install --upgrade pip

# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: npm install 很慢
```bash
# 使用国内镜像
npm config set registry https://registry.npmmirror.com
npm install
```

### Q: 前端访问后端 404
- 检查后端是否启动（http://localhost:8002/api/health 应返回 `{"status":"ok"}`）
- 检查 vite.config.js 的 proxy 配置是否和后端端口一致

### Q: API 调用失败
- 检查 .env 中的 DASHSCOPE_API_KEY 是否正确
- 确认 API Key 有调用相关模型的权限
- 查看后端控制台的错误日志

### Q: PowerShell 无法激活 venv
```powershell
# 方法1：修改执行策略
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 方法2：直接用 cmd
cmd
.\venv\Scripts\activate.bat
```

---

## 目录结构

```
视频生成/
├── backend/
│   ├── main.py              # 后端入口
│   ├── .env                 # 配置文件（不提交）
│   ├── requirements.txt     # Python 依赖
│   ├── routes/              # API 路由
│   ├── services/            # 业务逻辑
│   └── uploads/             # 上传文件目录
├── frontend/
│   ├── package.json         # Node 依赖
│   ├── vite.config.js       # Vite 配置
│   └── src/                 # React 源码
│       ├── pages/           # 页面组件
│       └── services/        # API 调用
└── README_WINDOWS.md        # 本文件
```

---

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/r2v` | POST | 参考图生视频 |
| `/api/t2v` | POST | 文生视频 |
| `/api/task/{id}` | GET | 查询任务状态 |
| `/api/optimize/prompt` | POST | 优化提示词 |
| `/api/optimize/script` | POST | 拆分脚本 |
| `/api/tts` | POST | 生成配音 |
| `/api/upload` | POST | 上传文件 |

---

## 技术支持

- 阿里云 DashScope 文档：https://help.aliyun.com/zh/dashscope/
- 问题反馈：（填写你的联系方式）

---

**最后更新**：2026-09-21
