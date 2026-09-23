# AI — Anime Information

**专为整理本地动漫文件而生。**

An Agent Skill for organizing local anime files. It teaches an AI agent to inspect anime libraries, understand common release naming, match external subtitles, and propose safe, reviewable file operations.

这是一个 **Anime File Organization Skill**，不是播放器、媒体服务器、下载器、追番订阅管理器，也不是完整的番剧管理平台。项目以 `anime-information/SKILL.md` 和领域参考资料为核心，尽量复用 Agent、Shell、成熟解析器及媒体工具的能力。

本项目以 **Mozilla Public License 2.0 (MPL-2.0)** 发布，详见 [`LICENSE`](LICENSE)。

## 结构

```text
Anime-Information/
├── anime-information/       # Skill directory; matches SKILL.md name
│   ├── SKILL.md
│   ├── references/
│   ├── scripts/
│   └── tests/
├── README.md
└── LICENSE
```

## 使用

Skill 位于仓库内的 `anime-information/` 目录（与 `SKILL.md` 中的 `name` 一致）。将该目录提供给支持 Agent Skills 的 Agent，并要求它整理指定的本地动漫目录。Agent 应遵循 `anime-information/SKILL.md`：先扫描和识别，再生成完整计划；默认只预览，得到明确确认后才执行。

只读扫描脚本可独立运行：

```bash
python3 anime-information/scripts/inspect_anime.py /path/to/anime
```

脚本向标准输出生成 JSON，不修改文件。若环境已安装 `anitopy`，脚本会使用它解析常见发布名；未安装时会使用保守的轻量回退解析。解析结果仅供参考。

运行脚本测试：

```bash
cd anime-information
python3 -m unittest discover -s tests -v
```

## 设计原则

```text
Small Skill > Large Application
Domain Knowledge > Hardcoded Logic
Reuse Existing Tools > Reinvent Everything
Dry Run > Blind Automation
Filesystem First > Media Server First
```

当前项目刻意不包含 Web UI、数据库、Docker、下载器、媒体播放或媒体服务器集成。
