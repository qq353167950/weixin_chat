# 离线前端依赖（随安装包分发）

本目录文件由本地 Flask 以 `/vendor/<文件名>` 提供，**不访问外网**。

| 文件 | 来源 | 许可 |
|------|------|------|
| `lucide.min.js` | [lucide](https://github.com/lucide-icons/lucide) UMD `0.469.0` | ISC |

更新 Lucide（需可访问 npm CDN）：

```bash
curl -fsSL -o assets/vendor/lucide.min.js \
  https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js
```

换版本后请同步改 `gui/build_redesign.py` 注释与本表版本号，并执行：

```bash
python gui/build_redesign.py
```
