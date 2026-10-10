# 可选模型的本地预检

发行配置中的 `face/object` 默认关闭。开发机已在固定GPU镜像上完成OWLv2真实probe、主站API与区域预览验收，并明确启用object；人脸模型未下载、仍关闭。运行版本与证据见[验收记录](../../docs/71-可选人脸与物体检索验收边界.md)。后续源码修补必须重新构建、probe及重建不兼容的旧索引，不能直接继承旧镜像收据。连接地址、密钥、能力配置都不等于模型可用。主站、Bridge API、提取子进程和查询子进程都拒绝未验收能力，视觉/声音默认流程不受影响。删除已有特征不需要重新取得模型权重，不被预检阻断。

## 当前状态边界

- 配置关闭：`capability_disabled`，不读取模型文件、不请求网络。
- 配置允许但缺清单、用途记录、依赖或权重：返回具体原因，保持 `ready=false`。
- 清单、许可用途记录、依赖、固定缓存与SHA-256全部核对通过：`model_validation_required`，仍为 `ready=false`。
- OWLv2 另须显式运行下述 probe，通过后产生由本地服务所有者私钥签名的验收收据。manifest中填写 `ready=true`、`inference_verified=true` 或另写无签名验收JSON都不能开放模型。开发机已取得真实GPU运行收据；其他机器与不同实现仍需独立执行，不能复制该结论作为验收。
- 人脸仍没有接受推理验收的协议，即使文件与用途许可记录齐全也保持 `model_validation_required`；物体验收收据不能用于人脸。

## 固定 OWLv2 准备与显式验收

官方版本固定为 `cfd3195ba4ea9592eec887ded089f4c08eff231d`。文件锁见 [`scripts/object-model.lock.json`](scripts/object-model.lock.json)：8个文件、共621,505,532字节，其中 `model.safetensors` 为619,918,824字节；其余为配置、预处理及词表。只准备safetensors，不再下载.bin。[固定官方文件列表](https://huggingface.co/google/owlv2-base-patch16-ensemble/tree/cfd3195ba4ea9592eec887ded089f4c08eff231d)

在独立的WISE运行环境中操作；服务保持离线。下列命令前两步只打印或校验，只有 `--download` 允许该工具进程联网。权重已存在时不需要重复下载。

```sh
python scripts/prepare_object_model.py
python scripts/prepare_object_model.py --verify-cache
python scripts/prepare_object_model.py --download
```

脚本逐文件核对固定字节数/SHA-256，全部成功后仅更新该OWLv2缓存的`refs/main`。`--verify-cache`不更新ref、不写清单、不联网。下载前检查完整预期体积加磁盘保留量；这不是文件系统硬配额。

许可用途已由操作者确定后，可显式添加 `--write-manifest /data/optional-models/object.json --purpose development_evaluation --license-reference "实际许可核对依据"`（object本机开发验收用`development_evaluation`；实际商业或非商业研究用途分别用`commercial`或`noncommercial_research`，不能混作许可声明）。脚本记录实际依赖精确版本，以独占创建方式防止覆盖已有用途记录。示例中的依据文字需要替换为真实审查记录，不表示程序替操作者授予或判定许可。

在该**同一WISE Python环境**及已确定用途的环境变量下，显式运行：

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 WISE_OPTIONAL_USE=development_evaluation \
python -m wise_bridge.optional_probe --capability object --image /owned-fixture/cup.png
```

输入是操作者提供、拥有使用权的本地图片（≤4MiB），最好含清晰物体。工具拷贝并去除元数据、生成4秒2fps视频，在独立受超时/磁盘/log预算约束的子进程内执行真实WISE提取、FAISS索引和**生产同一搜索函数**。检查完整8帧chunk的时间戳、图片/视频均有真实向量、索引与元数据一致、文字查询、检测框作为参考区域查询、源白名单/空白名单、稳定向量ID、有限合法区域、图片null时间及视频时间。没有检测到可用对象或任一步失败就不签发收据。工具不伪造权重、向量或检测结果，也不降低objectness阈值来凑通过。

生产与probe共用固定commit及完整函数hash约束的bbox元数据兼容修正，避免在probe里绕过生产路径的问题。检测完整率、语义准确率、空物体提示与用户任意框选空区域仍须后续独立验收；开发机短样本删除及浏览器区域交互已有独立证据；本工具是技术推理通过门槛，不是产品质量认证。

## 收据与信任边界

每次probe新建私有临时目录与随机nonce，执行全部阶段后，驱动程序只接受该次子进程输出；没有“签署已有JSON”的CLI或HTTP接口。模型清单摘要、依赖版本、WISE commit、相关桥接实现文件摘要、Python/平台与提取配置均绑定到收据；同一组内容还生成`feature_fingerprint`用于索引复用和查询一致性检查；模型变更、同大小替换、依赖/配置/实现变化会使旧收据失效。实际推理设备从真实提取器读取，必须与明确配置一致。旧快照的特征签名不符时要求重建；常驻查询进程遇同一能力的新签名时重启模型子进程，不能把新模型用于旧向量或悄悄继续用旧encoder。

私钥保存在 `/data/.optional-probe/attestation.key`（Linux 0600），收据原子写入同目录。HTTP仅校验签名，不签署；只有验证通过且服务显式配置允许的object才返回ready。主站只接受认证过的私有Bridge及受支持的收据协议，裸ready字段不会打开入口。face保持未验收。probe本身不会修改常驻服务开关或配置文件。

这不是抵抗有主机管理权限者的远程证明：能读取该私钥或替换服务代码的管理员本来就能改变服务。保护的是手填/篡改验收JSON和意外的模型/代码漂移。不要把私钥放进Git、浏览器响应或普通日志。

输入副本和临时索引在probe结束后清理，仅保留私有阶段日志及小型证据；失败日志不能当通过证明。网络audit只作用于明确的可选推理子进程。首次完整性检查会读取权重，但随后按文件身份复用摘要，健康检查不导入torch或分配GPU。

2026-10-04准备阶段：独立下载容器完成固定8文件校验，另一个`--network none`工具容器只读复验通过。临时用途记录下的只读预检耗时0.475秒、热0.0093/0.0085秒，`torch`未导入；该计时为进程内摘要缓存的冷/热，非冷操作系统磁盘测量，也还不是ready HTTP延迟。证据 `.runtime/media-integration/wise-object-prepared.json`。该准备阶段没有写入共享`/data`或启用配置；后续GPU验收阶段已另行记录开发测试用途、运行probe并启用object，详见上述验收记录。

## 只读清单

清单路径由 `WISE_OPTIONAL_MANIFEST_DIR` 指定，默认 `/data/optional-models`，每项能力一个 `face.json` 或 `object.json`。清单不接受任意模型路径、URL或执行命令；模型只从固定提取器实际使用的缓存位置读取。

必需字段如下；示意值不是已审查过的模型记录，不能作为可用部署清单。

```json
{
  "schema_version": 1,
  "capability": "object",
  "feature_id": "transformers/owlv2/google/owlv2-base-patch16-ensemble",
  "upstream_commit": "fcfa443fbb46eb361bb19151339338616838a5b5",
  "revision": "REPLACE_WITH_VERIFIED_40_HEX_HF_REVISION",
  "license_record": {
    "purpose": "RECORD_THE_ACTUAL_USE",
    "license": "Apache-2.0",
    "reference": "RECORD_THE_OPERATOR_REVIEW_REFERENCE"
  },
  "dependencies": {
    "transformers": "EXACT_INSTALLED_VERSION",
    "torch": "EXACT_INSTALLED_VERSION",
    "torchvision": "EXACT_INSTALLED_VERSION",
    "numpy": "EXACT_INSTALLED_VERSION",
    "sqlalchemy": "EXACT_INSTALLED_VERSION"
  },
  "files": [
    {"name": "config.json", "bytes": 1, "sha256": "REPLACE_WITH_VERIFIED_SHA256"}
  ]
}
```

- `WISE_OPTIONAL_USE` 默认 `unspecified`。只有在实际用途已确定时才记录 `commercial` 或 `noncommercial_research`；Apache-2.0的object额外接受本机开发验收用途`development_evaluation`，此值不能用于face；代码不会替用户认定许可用途。清单许可记录必须与该用途一致，且保留操作者审查依据。
- OWLv2固定模型卡标为Apache-2.0。缓存为 `$HF_HUB_CACHE/models--google--owlv2-base-patch16-ensemble/`，未设置HF_HUB_CACHE时使用`$HF_HOME/hub/`；`refs/main`必须等于清单固定revision，模型在对应`snapshots/<revision>/`。必需`config.json`、`preprocessor_config.json`、`tokenizer_config.json`、`special_tokens_map.json`、`vocab.json`、`merges.txt`、`model.safetensors`，可额外包含`tokenizer.json`与`added_tokens.json`。每个文件需要精确bytes与SHA-256，实际快照内文件全集必须与清单一致；新增未列入的tokenizer/config/weight也会失效，不同时准备.bin副本。[官方模型卡](https://huggingface.co/google/owlv2-base-patch16-ensemble)
- 默认InsightFace模型包仅支持固定 `buffalo_l`，路径`$HOME/.insightface/models/buffalo_l/`。必需`det_10g.onnx`、`w600k_r50.onnx`、`genderage.onnx`；如保留包内`1k3d68.onnx`/`2d106det.onnx`也必须逐项登记，拒绝额外模型文件和包描述文件。依赖清单须精确记录`insightface`、`onnxruntime`、`opencv-python`、`torch`、`numpy`、`sqlalchemy`版本。
- InsightFace官方模型许可为非商业学术研究；商业用途需要另行获得许可。研究用途记录标识`InsightFace-NonCommercial-Research`，商业用途必须记录实际的独立授权依据，标识`Separately-Granted-Commercial-License`。**这是操作者提供的事实记录，不是程序授予或认定商业许可。** 没有授权依据时保持未准备；不为方便测试虚构许可。[InsightFace官方说明](https://www.insightface.ai/)
- 清单必须固定当前WISE源码commit和feature ID。更换文件、固定revision、依赖或清单使预检状态失效。摘要缓存按实际路径、设备、inode、size、mtime_ns、ctime_ns复用；替换同大小文件并恢复mtime也会重新校验。它不是抵抗恶意管理员篡改整个文件系统的安全边界。

## 离线与进程边界

预检只用标准库读取小清单、文件状态、安装依赖元数据和文件摘要，不实例化模型、不安装依赖、不下载缺失文件。要求`HF_HUB_OFFLINE=1`与`TRANSFORMERS_OFFLINE=1`；HF快照符号链接必须解析到该模型缓存目录内。

固定InsightFace使用Python requests下载GitHub ZIP，HF离线开关本身不能防止它。因此可选推理子进程另有Python网络audit保护，阻断socket连接/DNS/sendto，并禁止albumentations更新检查。保护只在可选模型子进程安装，主站和Bridge HTTP进程仍可正常工作；已有visual/audio不安装该hook。当前未验收门槛先于模型加载，缺权重时不会走下载路径。

这不等于系统级网络防火墙，也不保证任意原生扩展不能联网；正式部署应另对推理容器设置出站网络策略。新的实际probe工具也必须在相同离线/资源边界中执行。不要在HTTP服务中调用`enforce_optional_runtime`。

## 验证范围

`tests/test_optional_models.py`与`test_optional_probe.py`使用明确的假权重文件和真实文件系统，核对缺项/摘要变化/同大小替换、许可用途匹配、缺真实收据仍不ready、签名防篡改、代码/配置/依赖漂移、失败子进程不能生成ready、API拒绝入队/查询，以及独立Python子进程的网络阻断。测试中的有效签名仅验证收据协议，**没有调用人脸或物体模型**，不能作为模型效果或真实浏览器框选验收。

本次最终桥接全组147通过、4跳过（27.35秒，`.runtime/media-integration/wise-optional-probe-final-contract.xml`）；跳过项包含3个需上游运行环境的存储测试与Windows不适用的Unix权限测试。三个真实SQLite/FAISS存储测试随后在独立离线容器中通过（16.58秒），覆盖生产bbox元数据查询与同帧多框顺序，没有实例化模型。主站状态映射9项通过；Ruff通过。

## 物体模型设备与微批次

OWLv2预处理固定为960×960。固定上游每个视频chunk为8帧，DataLoader的`batch_size=None`不代表一次只推理1帧。为本机8GiB显卡，object提取在独立模型子进程中安装精确commit与原方法完整SHA约束的微批次包装，每次只把1帧交给原始`extract_image_features`，按原顺序拼回同一Features/metadata结构；不改模型、阈值或特征算法。生产提取、图片区域查询和probe共用此实现，策略及代码纳入`feature_fingerprint`。probe至少覆盖完整4秒/8帧chunk，不以更短视频替代该路径。是否足够显存仍须真实probe观察，不能仅凭微批次单测宣布可用。

object要求显式`device=cpu`或`cuda`（支持cuda:0），不能使用自动GPU回落。Compose CPU默认只为object指定cpu，GPU overlay只为object指定cuda；默认visual/audio设置保持。CUDA只读预检接受原生NVIDIA节点或Docker Desktop WSL的`/dev/dxg`，再用标准库ctypes调用CUDA Driver API的cuInit/cuDeviceGetCount，确认实际存在可用设备；模型子进程再检查torch CUDA可用性，不能仅凭WSL节点存在就接受旧收据。没有GPU时报不可用。CPU与GPU有各自依赖/配置指纹，必须分别验收，不能互用收据。预检与签名验证不导入torch。

能力变量`WISE_BRIDGE_CAPABILITIES`现在可由私有env显式指定，默认仍`visual,audio`。实际probe通过后才按部署流程记录用途并开启object；probe不自行改配置。face仍关闭。

2026-10-04实际设备预检对照：同一GPU镜像的独立工具容器，显式`--gpus all`返回cuda（0.221秒），不分配GPU返回`model_device_unavailable`（0.170秒），均未导入torch。新增WSL、原生仅控制节点、缺设备/缺driver回归，最新定向45通过/1跳过（2.48秒）；此结果不包含实际物体模型推理。
