# 本地语音转写服务

独立 Python 3.12 环境安装 `requirements.txt`，不与主 API 或视觉索引环境混装。默认监听本机；主 API 设置 `TRANSCRIBER_URL=http://127.0.0.1:8012`。部署到其他机器时在服务设置 `ASR_TOKEN`，主 API 设置对应 `TRANSCRIBER_TOKEN`，并用可信内网和 TLS 保护连接。

模型使用 Systran/faster-whisper-small，固定 revision `536b0662742c02347bc0e980a01041f333bce120`。下载完整的 config.json、tokenizer.json、vocabulary.txt、model.bin 到本地目录，设置 `ASR_MODEL` 指向该目录。本服务只读取本地模型，不会在请求中自动下载。model.bin 为 483546902 字节，SHA256 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`。

启动：`python -m uvicorn app:app --host 127.0.0.1 --port 8012 --app-dir services/transcriber`。从仓库根目录运行。首次任务加载 CPU int8 模型；健康检查显示模型是否已加载。更换模型时必须同时更新模型来源标识，不能沿用旧标识续接任务。

主 API 从固定视频版本提取 30 秒单声道 PCM 音频，逐段发送，保存检查点。服务不接受文件路径或任意 URL。输出是待核对草稿，不识别人物身份；暂未做跨片段上下文拼接，须特别核对 30 秒边界、重叠人声、音乐和方言。

模型及实现参考：https://github.com/SYSTRAN/faster-whisper
