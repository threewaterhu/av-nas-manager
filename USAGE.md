# 日常使用

固定路径：

- Source：`/Users/three_water/Downloads`
- NAS 挂载点：`/Volumes/video`
- NAS 目标目录：`/Volumes/video/AV/censored`

每次使用：

1. 启动 qBittorrent（如果尚未运行）。
2. 确认 NAS 已挂载到 `/Volumes/video`。
3. 进入项目并运行 Preview：

   ```bash
   cd /Users/three_water/Documents/Codex/2026-08-29/mac-python-nas-1-2-qbittorrent/av-nas-manager
   source .venv/bin/activate
   python main.py
   ```

4. 确认没有异常或 `NEEDS_REVIEW`，再执行：

   ```bash
   python main.py rename
   ```

5. 再运行 Preview：

   ```bash
   python main.py
   ```

6. 确认 NAS Planned action 安全后执行：

   ```bash
   python main.py upload
   ```

7. 查看最终报告，确认 `UPLOADED_VERIFIED` 或 `ALREADY_UPLOADED`。

遇到 `NEEDS_REVIEW` 时停止自动处理并人工核实。程序永远不会自动删除 Mac 本地视频。

整理 NAS 旧影片时，先只运行：

```bash
python main.py nas-rename-preview
```

确认 Preview 后才可显式运行 `python main.py nas-rename`。该命令只会在 `/Volumes/video/AV/censored` 原目录内修改文件 basename；遇到重复番号、metadata 冲突或目标冲突不会强制处理。
