# 批量自动硬字幕提取指导文档

## 背景

当前项目已经具备硬字幕提取的主要能力：

- 使用 `SubtitleDetect` 做文本框检测。
- 使用 `VideoSubFinder` 检测硬字幕时间轴。
- 使用 PaddleOCR 识别字幕文本。
- 使用现有 raw txt 去重逻辑生成 SRT。
- GUI 支持批量任务，但当前流程仍偏向手动标注字幕区域，并假设批量视频分辨率和字幕区域一致。

目标是在尽量复用现有项目能力的前提下，增加一个独立的批处理流程，用于来源较杂的视频批量自动生成 SRT。

## 第一版目标

第一版采用“自动 ROI + VideoSubFinder + 现有 OCR/SRT”的方案：

```text
批量视频
→ 每个视频自动识别字幕区域 ROI
→ 保存 ROI JSON
→ 转码为 VideoSubFinder/OpenCV 兼容 MP4 副本
→ 使用 ROI 调用现有 VideoSubFinder 流程，必要时回退到 frame_det
→ 复用现有 OCR 和 SRT 生成逻辑
→ 输出每个视频的 .srt
```

第一版不追求帧级时间精度，字幕时间轴以 VideoSubFinder 输出为准。

## 第一版非目标

第一版暂不做以下内容：

- 不做开始/结束边界附近逐帧 refine。
- 不重构 GUI 主流程。
- 不重写 OCR 引擎。
- 不重写 SRT 生成逻辑。
- 不要求逐帧级开始时间。
- 不要求人工逐个视频确认字幕区域。

## 新增模块建议

### `backend/tools/auto_subtitle_area.py`

负责自动识别单个视频的大致字幕 ROI。

建议流程：

```text
打开视频
→ 均匀抽样若干帧
→ 调用 SubtitleDetect().detect_subtitle(frame)
→ 收集文本检测框
→ 过滤明显非字幕文本
→ 按 y 轴生成横向候选，按 x 轴生成竖向候选
→ 选择最像字幕的候选区域
→ 加 padding 得到最终 ROI
→ 返回 ROI、confidence 和诊断信息
```

ROI 识别目标是“覆盖字幕区域且尽量减少误判”，不需要精确贴合每一行文字。

建议抽样策略：

- 视频时长小于 10 分钟：默认抽样 45 帧。
- 视频时长小于 30 分钟：默认抽样 90 帧。
- 视频时长小于 60 分钟：默认抽样 150 帧。
- 视频时长大于等于 60 分钟：默认抽样 240 帧。
- 抽样应覆盖全片，可将视频切成多个时间桶，每个桶内抽若干帧。

字幕候选打分可考虑：

- 候选方向：`horizontal` 或 `vertical`。
- y 坐标稳定性。
- x 坐标稳定性，用于竖向字幕候选。
- 出现次数。
- 跨时间分布。
- 时域覆盖率 TPR（Temporal Presence Rate）：`frame_hits / sampled_frames`。
- 横向居中程度。
- 宽度是否合理。
- 竖向候选是否窄而高、是否位于画面左右侧。
- 是否避开角落水印/台标。
- 是否不像片头标题或场景文字。

TPR 判定规则：

- `20% <= TPR <= 75%`：合格主字幕 ROI，TPR 得分最高。
- `TPR < 10%`：判定为随机噪声/背景文本候选，排除。
- `10% <= TPR < 20%`：作为低覆盖过渡区间保留候选但降低 TPR 得分。
- `TPR > 75%`：作为高覆盖候选保留，但随 TPR 升高持续降低 TPR 得分，不再直接判定为固定水印/台标。

ROI 应适当放宽：

- x 方向左右加 5%-10% 视频宽度 padding。
- y 方向上下加 30-80 像素，或 3%-8% 视频高度 padding。
- 英文双行字幕需要保留足够高度。

### `scripts/batch_auto_extract.py`

负责批量调度。

建议流程：

```text
接收视频文件或目录
→ 遍历视频
→ 如果已有 subtitle_area.json，则复用
→ 否则调用 auto_subtitle_area 生成 ROI
→ 低置信度视频记录 warning 或跳过
→ 创建 SubtitleExtractor
→ 设置 sub_area 为自动 ROI
→ 优先使用 VideoSubFinder 扫描策略
→ VideoSubFinder 无产出时回退到 frame_det
→ 生成 SRT
→ 记录成功、失败和输出路径
```

批处理脚本应支持断点续跑：

- 已存在 ROI JSON 时不重复识别 ROI，除非传入 `--force-roi`。
- 已存在 SRT 时默认跳过，除非传入 `--force-srt`。
- 单个视频失败不应中断整批任务。

### 命令行用法

第一版实现后，可从项目根目录运行：

```shell
python scripts/batch_auto_extract.py <视频文件或目录>

# 使用 -i 指定输入位置，使用 -o 指定输出位置
python scripts/batch_auto_extract.py -i D:/videos -o D:/subtitles
```

常用参数：

```shell
# 递归扫描目录
python scripts/batch_auto_extract.py D:/videos --recursive

# 输入与输出分离
python scripts/batch_auto_extract.py -i D:/videos --recursive -o D:/subtitles

# 重新识别 ROI，但保留已有 SRT 跳过逻辑
python scripts/batch_auto_extract.py D:/videos --force-roi

# 重新生成 SRT
python scripts/batch_auto_extract.py D:/videos --force-srt

# 提高自动 ROI 置信度阈值
python scripts/batch_auto_extract.py D:/videos --min-confidence 0.65

# 默认先转码，再用 OpenCV 调用 VideoSubFinder；如需直接从 FFmpeg 开始
python scripts/batch_auto_extract.py D:/videos --vsf-decoder ffmpeg

# 多个 ROI 候选同时入选时，指定标准招式字库目录用于最终字幕文本比对
python scripts/batch_auto_extract.py D:/videos --label-config-dir D:/autoCut/autocut/label_configs

# 禁用 VideoSubFinder 前的兼容转码，用于对比排查
python scripts/batch_auto_extract.py D:/videos --no-vsf-transcode
```

脚本会在每个视频旁边生成 `*.subtitle_area.json`，并在高置信度时继续调用 `SubtitleExtractor(scan_strategy="vsf")` 生成同名 `.srt`。
如果传入 `-o/--output`，`*.subtitle_area.json` 和最终 `.srt` 会写入指定输出目录；未传入时仍写在原视频旁边。
批处理默认会在 ROI 识别结束后，在项目 `output/<视频名>_vsf_input/vsf_input.mp4` 生成一个临时 H.264/yuv420p 兼容副本，供后续所有 VideoSubFinder 候选和 OCR 取帧共用；VideoSubFinder 默认先用 OpenCV 解码，失败且未产出结果时自动重试 FFmpeg；如果两个解码器仍失败，会保底改用 `frame_det` 扫描当前 ROI；最终 `.srt` 输出到原视频旁边或 `-o/--output` 指定目录。
当多个 ROI 候选同时达到置信度阈值时，批处理会为每个候选生成临时 SRT，提取文本后和标准招式字库做快速子串匹配；最终只复制匹配得分最高的 SRT 到目标输出位置，共享转码副本和其他候选临时结果会被删除。

## 需要的小改造

当前 `SubtitleExtractor.run()` 的扫描策略是隐式选择：

```text
有 sub_area + GPU + accurate → extract_frame_by_det()
有 sub_area + 其他情况 → extract_frame_by_vsf()
无 sub_area → extract_frame_by_fps()
```

为了让批处理稳定复用 VideoSubFinder，建议给 `SubtitleExtractor` 增加显式扫描策略字段：

```python
sr.scan_strategy = "vsf"
```

`run()` 中优先读取该字段：

```text
scan_strategy == "vsf" → extract_frame_by_vsf()
scan_strategy == "frame_det" → extract_frame_by_det()
scan_strategy is None → 保持当前自动选择逻辑
```

这样 GUI 原有行为保持不变，批处理脚本可以明确优先走 `自动 ROI + VideoSubFinder`，并在 VideoSubFinder 无产出时回退到 `frame_det`。

## 最大复用边界

应复用：

- `backend/tools/subtitle_detect.py` 的 `SubtitleDetect`。
- `backend/bean/subtitle_area.py` 的 `SubtitleArea`。
- `backend/main.py` 的 `SubtitleExtractor`。
- `backend/main.py` 的 `extract_frame_by_vsf()`。
- `backend/tools/subtitle_ocr.py` 的 OCR 队列和识别流程。
- `backend/main.py` 的 `_remove_duplicate_subtitle()`。
- `backend/main.py` 的 `generate_subtitle_file_vsf()`。

暂不复用为核心入口：

- GUI 中的选区继承逻辑。它适合同分辨率、同字幕位置的视频，不适合来源杂的视频自动批处理。
- 无选区时的 `extract_frame_by_fps()`。它是固定 FPS 抽帧，时间精度和召回都不适合作为目标主流程。

## ROI JSON 建议格式

建议每个视频旁边生成：

```text
video.mp4
video.subtitle_area.json
video.srt
```

示例：

```json
{
  "video": "video.mp4",
  "width": 1920,
  "height": 1080,
  "fps": 29.97,
  "frame_count": 123456,
  "subtitle_roi": {
    "xmin": 96,
    "xmax": 1824,
    "ymin": 760,
    "ymax": 980
  },
  "confidence": 0.86,
  "sampled_frames": 180,
  "method_version": "auto-roi-v1",
  "status": "ok",
  "selected_candidate_index": 0,
  "text_match_score": 128.0,
  "candidates": [
    {
      "roi": {
        "xmin": 180,
        "xmax": 1740,
        "ymin": 805,
        "ymax": 910
      },
      "score": 0.86,
      "hits": 212,
      "frame_hits": 212,
      "time_bucket_hits": 8,
      "orientation": "horizontal",
      "temporal_presence_rate": 0.3533,
      "temporal_presence_score": 1.0,
      "temporal_presence_label": "primary_subtitle",
      "excluded": false
    }
  ]
}
```

低置信度时可写：

```json
{
  "status": "low_confidence",
  "confidence": 0.42,
  "reason": "no stable subtitle band found"
}
```

## 第一版验收标准

- 能接收一个目录或多个视频文件作为输入。
- 每个视频自动生成 `*.subtitle_area.json`。
- 高置信度视频自动生成 `.srt`。
- 低置信度视频记录 warning 或跳过，不阻塞整批任务。
- 多 ROI 候选的最高字库匹配 `text_score` 和最高 `coverage` 都低于 `0.50` 时按低置信度跳过。
- 短时固定位置字幕可作为 `short_primary_subtitle` 候选进入 OCR；初始 ROI 无候选或字库匹配过低时会触发加密 ROI rescue。
- VideoSubFinder 无字幕输出时按 `no_subtitle` 统计，不作为错误中断整批任务。
- 原 GUI 流程不受影响。
- 当前已有 OCR、VideoSubFinder、SRT 生成逻辑被复用。
- 批处理有清晰日志，便于定位失败视频。

## 后续增强方向

如果第一版时间轴不够准确，再增加边界 refine：

```text
自动 ROI
→ VideoSubFinder 粗时间轴
→ 在每个 start/end 附近逐帧检测
→ 修正开始/结束时间
→ OCR/SRT 输出
```

该阶段可新增：

```text
backend/tools/subtitle_boundary_refiner.py
```

并复用：

- `SubtitleDetect().detect_subtitle(frame)`
- `get_coordinates()`
- `SubtitleArea`

边界 refine 的建议规则：

- 开始时间：从 VSF start 前后约 1 秒逐帧扫描，找到连续 2-3 帧有字幕的第一帧。
- 结束时间：从 VSF end 前后约 1 秒逐帧扫描，找到字幕连续消失前的最后稳定帧。

该增强不是第一版必需内容。
