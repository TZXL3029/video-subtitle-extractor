# 外部本地项目依赖清单

扫描日期：2026-07-30

项目根目录：`D:\video-subtitle-extractor`

## 扫描范围

- 扫描了仓库内文本文件，包含被 `.gitignore` 忽略的文本文件；排除了 `.git` 对象库。
- 重点扫描了 Windows 盘符路径、常见 Unix 绝对路径、`sys.path`/`PYTHONPATH` 注入、`dependencies`、`label_configs`、`autoCut`/`autocut` 等跨本地项目信号。
- `output/` 下存在大量运行生成物，路径均在当前仓库 `D:\video-subtitle-extractor\output\...` 内，未作为外部项目依赖计入。

## 确认的外部本地项目依赖

| 外部本地路径 | 依赖类型 | 使用位置 | 影响 | 备注 |
| --- | --- | --- | --- | --- |
| `D:/autoCut/autocut/label_configs` | 本地字库配置目录，疑似来自另一个本地项目 `autoCut/autocut` | `backend/tools/label_text_matcher.py:17`、`scripts/batch_auto_extract.py:60`、`README.md:224`、`README.md:261`、`docs/batch-auto-extraction-guide.md:158` | 批处理多 ROI 候选选择时默认从这里加载标准招式字库 JSON，用字幕文本匹配分数选择最终 SRT | 这是软依赖：目录不存在时 `load_label_matchers()` 返回空列表，脚本会告警并跳过字库评分；可用 `--label-config-dir` 指向其它目录 |

## 绝对路径扫描结果与判定

| 路径或模式 | 位置 | 判定 |
| --- | --- | --- |
| `D:/autoCut/autocut/label_configs` | 见上表 | 确认的外部本地项目/目录依赖 |
| `D:/videos`、`D:/subtitles` | `README.md:230`、`README.md:237`、`README.md:240`、`README.md:243`、`README.md:246`、`README.md:249`、`README.md:252`、`README.md:255`、`README.md:258`、`README.md:261`、`README.md:264`、`README.md:265`、`README.md:276`、`docs/batch-auto-extraction-guide.md:133`、`docs/batch-auto-extraction-guide.md:140`、`docs/batch-auto-extraction-guide.md:143`、`docs/batch-auto-extraction-guide.md:146`、`docs/batch-auto-extraction-guide.md:149`、`docs/batch-auto-extraction-guide.md:152`、`docs/batch-auto-extraction-guide.md:155`、`docs/batch-auto-extraction-guide.md:158`、`docs/batch-auto-extraction-guide.md:161`、`docs/batch-auto-extraction-guide.md:162`、`docs/batch-auto-extraction-guide.md:165` | 文档示例输入/输出路径，不是仓库依赖 |
| `D:/tools/video-subtitle-extractor-main` | `README.md:136`、`README_en.md:139` | 文档示例源码位置，不是外部依赖 |
| `D:\下载\vse\运行程序.exe`、`E:\study\kaoyan\sanshang youya.mp4` | `README.md:64`、`README.md:66`、`README_en.md:65`、`README_en.md:67` | 文档中的反例路径，用于说明不要使用中文或空格路径，不是依赖 |
| `D:\video-subtitle-extractor` | `logs/2026-07-29.md:5`、`logs/2026-07-30.md:5`、`docs/agent-runs/2026-07-29-short-subtitle-roi-rescue/PLAN.md:4`、`docs/agent-runs/2026-07-29-short-subtitle-roi-rescue/LOG.md:6`、`docs/agent-runs/2026-07-29-short-subtitle-roi-rescue/STATE.json:16` | 当前仓库根目录记录，不是外部项目依赖 |
| `C:\Users\fangyao\Downloads\test.mp4` | `backend/subfinder/windows/previous_video.inf:1` | 捆绑 VideoSubFinder 的历史状态/示例输入，不被项目 Python 代码引用，不是外部项目依赖 |
| `C:\test_video.mp4`、`C:\test_video.srt`、`C:\ResultsDir` | `backend/subfinder/macos/settings/Localization/chn/locale.cfg:98`、`backend/subfinder/macos/settings/Localization/eng/locale.cfg:98`、`backend/subfinder/macos/settings/Localization/rus/locale.cfg:98` | VideoSubFinder 帮助文本示例，不是仓库依赖 |
| `/home/yao/Videos/null.srt` | `backend/tools/reformat.py:218` | 仅在直接运行 `python backend/tools/reformat.py` 时使用的本地调试路径，不是主程序依赖；属于可清理的硬编码本地测试路径 |
| `D:\video-subtitle-extractor\output\...` | `output/testB16_roi_candidates/...`、`output/testB16_vsf_input/vsf_input.mp4` | 当前仓库内运行生成物，未被 Git 跟踪，不是外部项目依赖 |

## 额外路径注入检查

| 位置 | 内容 | 判定 |
| --- | --- | --- |
| `backend/__init__.py:3` | 将仓库根下的 `dependencies` 插入 `sys.path` | 项目内相对目录；当前 `D:\video-subtitle-extractor\dependencies` 和 `D:\video-subtitle-extractor\backend\dependencies` 均不存在，不是外部本地项目依赖 |
| `backend/main.py:28` | 将 `backend` 目录插入 `sys.path` | 项目内路径 |
| `backend/sushi/__main__.py:9` | 将 `backend` 目录插入 `sys.path` | 项目内路径 |
| `scripts/batch_auto_extract.py:20-22` | 将项目根目录插入 `sys.path` | 项目内路径 |

## 结论

当前确认依赖的外部本地项目/目录只有一个：`D:/autoCut/autocut/label_configs`。其它扫描到的绝对路径属于文档示例、当前仓库路径、捆绑工具帮助文本、运行生成物或本地调试路径。
