# PDF Translate

基于 Electron + Vite 的桌面应用，通过本地 Python 引擎服务翻译 PDF。
应用启动时在 127.0.0.1 拉起服务，通过 SSE 推送进度，完成后返回
Base64 编码的双语 PDF 供下载。

## 特性

- 本地 Python 常驻服务（不再每次点击都启动引擎）
- UI 实时进度
- 双语 PDF 下载（Base64 结果）
- 翻译服务：google / bing
- Windows 打包（GitHub Actions）

## 环境要求

- Node.js 24
- Python 3.12

## 开发运行

安装依赖：

```bash
npm ci
```

启动开发模式（renderer + Electron）：

```bash
npm run dev
```

使用流程：

1) 点击“选择 PDF”并选择文件
2) 选择服务（google/bing）
3) 点击“开始翻译”
4) 查看进度，完成后下载

## 本地服务（手动启动）

```bash
python -m pdf2zh_engine.server --port 0
```

STDOUT 首行输出：

```
{"type":"ready","port":12345}
```

其他日志与 traceback 输出到 STDERR。

## Windows 打包（GitHub Actions）

workflow：`.github/workflows/build-win.yml`

产物：

- `dist/*.exe`（安装包）
- `dist/win-unpacked/**`（免安装目录版）

## 架构概览

- Electron main：启动/守护 Python 服务，转发 HTTP + SSE
- Preload：暴露安全 API
- Renderer：UI（选择 PDF、开始、进度、下载）
- Python server：执行翻译并返回 Base64 PDF
