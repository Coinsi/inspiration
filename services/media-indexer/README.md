# 自托管视频检索推理服务

后端保存视频版本、采样帧、时间段、向量和人工标注。本服务仅把受限的文字/图片/视频采样帧转换成向量；不接收外部 URL，不保管项目原片，不替代权限检查。

## 运行

推荐独立 Python 3.12 环境，与业务后端分开安装 `requirements.txt`。CPU 可以运行；GPU 主机应按 PyTorch 官方方式安装对应 CUDA 的 torch/torchvision。

```powershell
uv venv --python 3.12 .runtime/media-indexer
uv pip install --python .runtime/media-indexer/Scripts/python.exe -r services/media-indexer/requirements.txt
$env:INDEX_MODEL = 'Qwen/Qwen3-VL-Embedding-2B'
$env:INDEX_TOKEN = 'replace-with-a-private-service-token'
$env:OMP_NUM_THREADS = '8'
.runtime/media-indexer/Scripts/python.exe -m uvicorn app:app --app-dir services/media-indexer --host 127.0.0.1 --port 8011
```

模型约 4.26 GB，第一次加载可能下载。可以预先下载到服务器磁盘，把 `INDEX_MODEL` 改为该目录；离线运行可设置 `HF_HUB_OFFLINE=1`。浏览器用户不需要安装该服务；它运行在平台服务器上。

业务后端配置 `LIBRARY_INDEXER_URL=http://127.0.0.1:8011` 与相同的 `LIBRARY_INDEXER_TOKEN`。同主机默认只监听回环地址；跨主机需要私有网络/认证代理并配置令牌。不要把无令牌的推理服务直接暴露到公网。

## 固定模型与协议

- 模型：`Qwen/Qwen3-VL-Embedding-2B`，revision `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda`。
- 本轮实际校验的 `model.safetensors`：4,255,140,312 bytes，SHA-256 `c73fa9caeddeb3ff831d46c085a7a5708343248ca777e90f2d486964464509c1`。
- 模型输出取最后一个非填充 token，MRL 前512维后归一化；索引记录完整模型键，不同模型键不混合检索。
- `EmbeddingModel` 保留权重的 `model.` 命名空间；缺失、多余或尺寸不匹配的权重会拒绝加载。不能换成裸 `AutoModel` 然后忽略随机初始化警告。
- 采样视频每帧最多384×256像素，并显式指定较小的最小像素限制；每次最多16帧，输入有大小和token上限。推理串行，避免同时装入多个模型。
- `/health` 返回模型键、维度、是否已加载；`/embed` 接受 `text`、base64 `images`、`video` 和采样 `fps`。模型尚未加载时的健康响应不代表推理已经通过。

修改模型、revision、维度或采样表示时必须使用新的模型键并重建索引。自定义模型可配置 `INDEX_MODEL_ID`、`INDEX_MODEL_REVISION`，需要重新做质量检查。

## 本轮验证边界

在本机 CPU 上，3张公开图片制作的12秒视频完成实际采样、推理、入库、中文检索与图片检索；对应来源均排第一。该小样本证明端到端链路，不能代表动作、跨镜头人物或长片漏检率。自动重排、ASR、物体检测和跨集人物身份不在本服务中冒充实现。

参考：[Qwen 官方实现](https://github.com/QwenLM/Qwen3-VL-Embedding)、[PyTorch 安装](https://pytorch.org/get-started/locally/)。模型权重按其 Apache-2.0 许可单独获取，不随本仓库提交。
