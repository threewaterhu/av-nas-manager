# av-nas-manager v1.0.0

一个保守的本地 Python 命令行工具，用于扫描 qBittorrent 已完成的视频、识别番号、查询并缓存元数据、标准化本地文件名，以及去重后可靠上传到 NAS。

## 环境要求

- macOS 与 Python 3.11+
- qBittorrent Web UI/API 已启用，并限制为本机访问
- NAS 已通过 Finder/SMB 挂载
- 无第三方运行时依赖

当前路径：

- Source：`/Users/three_water/Downloads`
- NAS 挂载点：`/Volumes/video`
- NAS 目标目录：`/Volumes/video/AV/censored`

## 配置

首次使用时复制 `config.example.yaml` 为 `config.yaml`。实际配置不会进入 Git。

主要配置包括：

- `source_directory`
- `nas_mount_path`
- `nas_target_directory`
- `min_video_size_mb`，当前为 200
- `dry_run`
- `max_actresses_in_filename`
- qBittorrent host、port、username、password
- `upload.verification_mode`，V1.0 仅支持 `size`

密码只填写在本机 `config.yaml`，不要提交或粘贴到聊天中。

## 命令

激活虚拟环境：

```bash
source .venv/bin/activate
```

普通诊断、重命名 Preview 和上传 Preview：

```bash
python main.py
```

显式执行本地同目录重命名：

```bash
python main.py rename
```

显式执行 NAS 上传：

```bash
python main.py upload
```

仅修改 `dry_run` 永远不会触发 rename 或 upload。

## 完整处理流程

1. 从 Source 扫描不少于 200 MiB 的常见视频文件。
2. 通过 qBittorrent 具体文件 `progress == 1.0` 确认下载完成。
3. 识别标准番号并进行 cache-first 元数据查询。
4. Preview 建议文件名和 NAS 上传动作。
5. 用户显式运行 `rename`，程序完成 preflight 后仅修改 basename。
6. 再次 Preview，确认文件已标准化并检查 NAS 去重状态。
7. 用户显式运行 `upload`，程序写入唯一的 `<正式名>.uploading`。
8. 字节大小验证通过后，在 NAS 同目录改成正式文件名并再次验证。
9. SQLite 保存 metadata 与上传状态；NAS 实际状态始终是最终依据。

## 主要状态

- `COMPLETE`：qBittorrent 具体文件已 100% 完成。
- `UNKNOWN_NOT_IN_QBITTORRENT`：无法与 qBittorrent 具体文件可靠匹配。
- `READY`：preflight 通过，可以执行下一步。
- `ALREADY_STANDARDIZED`：本地文件名已符合规则，不重复 rename。
- `ALREADY_UPLOADED`：NAS 已存在同番号、同字节大小的正式文件。
- `UPLOADING`：正在写入唯一的 `.uploading` 临时文件。
- `UPLOADED_SIZE_OK`：临时文件与本地源字节大小一致。
- `UPLOADED_VERIFIED`：正式 NAS 文件已经最终验证。
- `RECOVERED_COMPLETED_UPLOAD`：完整临时文件已安全恢复为正式文件。
- `FAILED`：操作失败，保留现场供诊断。
- `NEEDS_REVIEW_*`：存在大小冲突、重复文件或无法确认的临时状态。

遇到任何 `NEEDS_REVIEW` 状态时，不要强制覆盖、删除或擅自选择文件，应先人工核实。

## 安全保证

- 程序永远不会自动删除 Mac 本地视频。
- 不覆盖无法确认的 NAS 正式文件。
- 不自动删除同番号重复文件。
- 只删除经过严格确认、位于目标目录且小于本地源的精确 `.uploading` 临时文件。
- 不删除 torrent，不修改 qBittorrent 设置。
- 不包含 GUI、定时任务、自动删除或强制 SHA-256。

## 测试

```bash
python -m unittest discover -v
```
