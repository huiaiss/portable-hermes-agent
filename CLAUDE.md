# Auto Video Platform — 当前状态

> 🔍 Hermes 审计时间：2026-05-24 22:38
> 上次审计：2026-05-24 架构全面体检

## ⚠️ 当前架构问题（需优先处理）

### P0 — 分轨规则大面积违规
自己定的"平台和内容分离"规则被打破。""AI照妖镜"" 硬编码在 8 个 .py 文件里：
- pipeline.py, builders/assembly_engine.py, builders/components.py
- builders/jianying_exporter.py, builders/storyboard_mapper.py
- generators/script_engine.py, generators/script_generator.py
- generate_ep2_assets.py

**影响**：隆江自动化品牌配置无法接入，平台被锁死在 AI照妖镜。

### P0 — 测试全部是假的
4 个测试文件，0 个 assert。"跑一遍看崩不崩"不是测试。

### P1 — components.py 已膨胀到 969 行
每加一种视频风格，这个文件继续膨胀。需要组件注册机制。

### P1 — DESIGN.md 路线图过时
Phase 1 分析器状态与 CLAUDE.md 描述不一致。

### 未提交改动
5 个文件有未 commit 的修改，需立即提交。

## ✅ 已解决的
- app.py (Gradio UI) 已删除 ✓
- git init 已完成 ✓
- 分轨模板 episode_template.yaml 已创建 ✓

---

## 环境

- Windows 10 Pro, RTX 3060 Ti 8GB, Python 3.14
- ComfyUI: http://127.0.0.1:8188 (conda comfyui)
- FFmpeg NVENC ⚠️ HEVC+setpts=黑屏, 可靠: eq/scale/crop/drawtext/fps
- DeepSeek API (deepseek-v4-pro 防401)
- 素材: D:\隆江视频素材\ (34/35/36_raw.MP4 竖屏)
- 5 个文件有未提交改动

---|------|------|
| 素材分析层 | ✅ 8个检测器已实现 | sharpness/composition/color/stability/face/hand/text/texture |
| 方案生成层 | ✅ 可用 | script_generator.py (31KB) + script_engine.py (23KB) |
| LLM Provider | ✅ DeepSeek+Qwen | config/llm_config.json，支持 fallback |
| TTS配音 | ✅ Edge TTS 为主 | 6种中文音色可选，CosyVoice/Coqui/OpenAI 备用 |
| BGM系统 | ✅ 8首曲库 | bgm_library/ + bgm_mixer.py |
| 字幕引擎 | ✅ | subtitle_engine.py，SRT生成+叠加 |
| 视频渲染 | ✅ Chromium headless | 非 HyperFrames，实际用 Chromium → MP4 |
| Web UI | ✅ FastAPI | web/main.py (端口可配) + static editor |
| Gradio UI | ✅ 备选 | app.py (Gradio，功能更全) |
| 剪映导出 | ✅ | builders/jianying_exporter.py (33KB) |
| E2E测试 | ✅ | test_all_stages.py, tests/benchmark_*.py |

### 当前Brand DNA

- 运行中配置: **AI照妖镜** (config/brand_dna.json)
  - slogan: "反诈APP给你答案，我给你眼睛"
  - 风格: 深色+霓虹绿扫描线+红色圆圈标注+赛博朋克
- 隆江品牌: 尚未创建配置文件，DESIGN.md 中有 YAML 示例待落地

### 已完成输出

- EP1 (AI破绽-皮肤纹理): ep1_skin_v3 (2.8MB), ep1_stock_test (3.9MB)
- EP2 (AI破绽-文字乱码): 13.1MB 成品
- EP3 (AI破绽-v2): 0.9MB
- BGM测试: test_bgm_final/per_beat/multi 均有产出
- 渲染质量迭代记录: output/_test_20260524_* (多个版本，从3.4MB→6.6MB)

### 待做

- [ ] 为隆江自动化创建 Brand DNA 配置
- [ ] 接入真实工厂素材（D:\隆江视频素材\）跑企业宣传视频
- [ ] 发布层（多平台策略生成）尚未开发
- [ ] Quality Gate 审核层 + Batch Scheduler 未开发
- [ ] 视频素材提取和分析管线（目前以图片分析为主）
- [ ] CosyVoice 词级时间戳（配置中有但未启用）
- [ ] 场景分类器 (analyzers/ 中无 scene_classifier.py)

## 用户偏好

以下偏好 **每次会话必须遵守，不可商量**：

1. **先出方案架构再动手** — 不接受边做边试。做视频要先给：定位策略→脚本分镜→技术路径→交付清单，全确认后才执行。
2. **效果导向** — "根本达不到要求"就推翻重来，不凑合。
3. **中文优先** — 所有输出、脚本、字幕、TTS 全中文。
4. **简单直接** — 用户是工厂老板，编程小白，不要讲技术细节，给结果。

## 用户背景

- 台州隆江自动化设备老板，生产无刷电机绕线机
- 想做产品宣传短视频和短剧
- 已有 Claude Code (主力开发 Agent)
- 视频素材在 D:\隆江视频素材\

## 踩过的坑

### FFmpeg 致命问题
- **HEVC (h265) 手机视频 + setpts 滤镜 = 黑屏输出**
- 所有 setpts 变体都黑: setpts=2\*PTS, setpts=PTS\*2, setpts+fps 组合, -r 15
- 解决方案: (1) 跳过慢动作直接用原速 (2) PNG帧序列重建 (3) -filter_complex 替代
- 可靠滤镜: eq, scale, crop, drawtext, fps(原速)
- 视频黑屏先试 `-profile:v baseline -movflags +faststart` 重编码

### MediaPipe
- 手势检测在低光照/模糊素材下准确率断崖式下降
- 需要先过 sharpness_detector 过滤低质量素材

### 渲染
- Chromium headless 渲染大分辨率时可能内存溢出
- 复杂 HTML 动画用 webm 中间格式再转 MP4 更稳定

## 关键设计决定

1. **素材驱动，不预设脚本** — 先分析素材再生成方案
2. **配置全部 JSON/YAML 驱动** — 零硬编码
3. **Chromium 渲染非 HyperFrames** — 实际渲染引擎是 Chromium headless
4. **DeepSeek 为主 LLM** — fallback 链: DeepSeek → Qwen → Ollama
5. **Edge TTS 为主 TTS** — 免费高质量，不需要 API key
6. **中文全链路原生** — OCR/TTS/字幕/脚本全中文
7. **剪映导出作为分发通道** — jianying_exporter.py 支持导出到剪映编辑

## 项目结构速览

```
auto-video-platform/
├── CLAUDE.md              ← 本文件（AI 工作手册）
├── DESIGN.md              ← 产品愿景+架构设计（参考用，不自动加载）
├── pipeline.py            ← 主管线
├── app.py                 ← Gradio Web UI
├── web/main.py            ← FastAPI Web UI
├── analyzers/             ← 8个检测器
├── generators/            ← 脚本生成+TTS+LLM
├── builders/              ← 视频合成+渲染+BGM
├── config/                ← 运行时配置（brand_dna, llm, tts）
├── configs/               ← 预设配置（品牌+视频类型）
├── bgm_library/           ← BGM曲库(8首)
├── output/                ← 输出目录（含20+测试成品）
├── tests/                 ← 测试
└── docs/                  ← 调研文档
```

## 启动方式

```bash
# Web UI
cd D:/auto-video-platform
python web/main.py

# Gradio UI
python app.py

# 直接跑管线
python pipeline.py

# E2E测试
python test_all_stages.py
```


## AI照妖镜 单集完成标准

以下清单全部打勾，一集才算完成。Claude 每跑完一轮自己检查。

- [ ] 视频分辨率 1080×1920，竖屏，无黑边
- [ ] TTS 口播清晰、语速自然（speed 1.0-1.1），无机械感/破音
- [ ] AI破绽标注圆圈位置准确，不偏移、不抖动
- [ ] 真假对比画面切换流畅，对比段至少3秒
- [ ] 字幕与口播同步（容差 0.2秒），无错字/漏字
- [ ] BGM 不盖过人声（人声/BGM 比例 ≥ 3:1）
- [ ] 片尾有 slogan："反诈APP帮你查，AI照妖镜教你看。反诈APP给你答案，我给你眼睛"
- [ ] 总时长 30-60 秒
- [ ] 视频文件可正常播放，无花屏/黑帧/音画不同步

## 平台 vs 内容 分轨规则（铁律）

### 内容生产模式（做新一集时）
- **只改 YAML 配置文件 + 换素材图片，不改任何 .py 文件**
- 新一集 = 复制 configs/episode_template.yaml → 填新内容 → 跑 pipeline
- 改字幕大小 = 改 YAML 里的 font_size_body
- 改颜色 = 改 YAML 里的 annotation_color
- 改口播文案 = 改 YAML 里的 script 段落

### 平台开发模式（改进平台功能时）
- 改 .py 文件 = 平台开发
- 平台开发前先 git commit 当前状态
- 平台开发后跑全部测试确认没把内容管道搞崩
- 一次只改一个模块

### 判断规则
问自己：这个改动是"这一集想要不一样"还是"所有集都应该这样"？
- 单集需求 → 改 YAML
- 平台需求 → 改 Python（谨慎，小步，测试）


## Hermes 协作模式（铁律）

### 角色分工
- **Claude Code** = 主力程序员。拿到清晰任务 → 高效写代码。
- **Hermes** = 项目记忆 + 架构监理。记住全局、审计偏差、给出方向。

### 协作流程

**Hermes 做的事（本文件由 Hermes 维护）：**
- 定期审计代码 vs CLAUDE.md 承诺是否一致
- 发现偏差 → 更新 CLAUDE.md 标出问题
- 输出状态报告，写到本文件顶部

**Claude 做的事：**
- 每次会话开始 → 读本文件 → 了解真实状态
- 根据状态报告决定优先级
- 写代码前检查：有没有架构债务要先清理

### 调度 Hermes
```bash
# Claude 在终端执行，同步获取结果
hermes chat -q "自包含任务描述"

# 示例
hermes chat -q "审计 builders/components.py，报告拆分建议"
hermes chat -q "检查所有.py中硬编码的'AI照妖镜'，报告文件和行号"
hermes chat -q "对比 CLAUDE.md 待做清单和实际完成状态"
```

### 为什么不用 MCP 消息桥

消息桥模式：Claude → `messages_send` → 等待 Hermes → `events_poll` 取结果。异步两跳。

问题：
1. **回合效率差**：发消息→等地→取结果，3个工具调用才完成一次对话。60秒的事变3分钟。
2. **容错差**：Hermes 没反应时没有超时报错，Claude 干等不知道卡在哪里。
3. **MCP 连接不稳定**：hermes mcp serve 进程常挂，重启后 session 不持久化。
4. **调试困难**：出问题时不知道是 MCP 挂了、Hermes 没收到、还是回复丢失。

命令行模式：`hermes chat -q "任务"` 一次同步调用，30-60秒出结果。简单直接。

### 使用方式
- 不要用 MCP 消息桥，直接命令行调用
- Hermes 有持久记忆，不需要重复解释项目背景
- Hermes 的审计报告会直接写进本文件

---

## 开发纪律

1. **git commit 是呼吸，不是负担。** 每次改代码前先 commit，改完验证通过再 commit。
   当前仓库无 git → 立即 `git init && git add -A && git commit -m "初始版本"`

2. **删掉 Gradio UI。** 只保留 FastAPI（web/main.py）。
   两个 UI 维护成本翻倍，bug 翻倍。Gradio 相关代码：app.py + app/ 整个目录。

3. **不要往 components.py 里加内联 HTML。** 
   改样式 = 改 YAML。只有新增组件类型时才能动 components.py。

4. **测试必须有断言。** "跑一遍看崩不崩"不是测试。
   至少验证：输出文件存在、分辨率正确、时长在范围内。
