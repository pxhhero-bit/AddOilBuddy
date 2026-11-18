---
name: add-oil-buddy
description: 在本机创建/管理一个 WorkBuddy 内置定时任务，每天自动领取 WorkBuddy 客户端「Buddy 加油站」的每日积分（无人值守 RPA）。当用户提出「每天定时领 Buddy 加油站积分」「自动领取每日积分」「建一个领积分的定时任务」「把加油站领取自动化」等需求时应使用本 skill。也用于对该任务的检查、改时间、卸载。仅适用于 Windows 上已安装 WorkBuddy 桌面客户端的机器。
agent_created: true
---

# Buddy 加油站每日积分自动领取

## 概述

WorkBuddy 客户端的「Buddy 加油站」每日积分没有公开 API，只能走桌面 UI。本 skill 提供一套
纯像素判据的本机 RPA 脚本（不用 UIA 扫描、不注入键盘），并指导 agent 用 WorkBuddy 内置的
定时任务能力把它挂到每天的指定时间。

整套方案分两层，务必分清职责：

- **脚本层（Python，`scripts/`）**：真正执行领取的 RPA 程序 + 一个环境探测/参数生成器
  （`setup.py`）。脚本**不能**自己创建定时任务——`automation` 是 agent 的工具，Python 调不到。
- **调度层（agent，即你自己）**：运行 `setup.py` 拿到参数，然后用 `automation_update` 工具
  创建/修改定时任务。**这一步只能由你做。**

## 前置条件（开工前先确认）

1. **平台**：Windows。脚本依赖 Win32 API（`ctypes.windll`）、`PrintWindow`、鼠标钩子。
   macOS / Linux 不适用，直接如实告知用户。
2. **WorkBuddy 桌面客户端**已安装且能登录。RPA 靠窗口标题 `WorkBuddy` /
   窗口类名 `Chrome_WidgetWin_1` 找窗口；找不到窗口任务会失败但不会误报成功。
3. **Python 3.8+ 且已装 Pillow**。Pillow 缺失时由 `setup.py --ensure-deps` 自动安装
   （`python -m pip install Pillow`），无需用户手动处理；只有完全找不到 Python 时才需用户先装。
   `uiautomation` 是可选依赖（仅失败诊断用，缺失不影响领取）。
4. **授权边界**：本方案会移动鼠标并做真实点击，但只在 WorkBuddy 窗口内执行，
   执行完把鼠标归位，且开头有 5 分钟倒计时提醒窗给用户反应时间。
   若用户明确表示「不要接管我的鼠标/键盘」，**停止**，本 skill 不适用。

## 工作流

### 第 0 步：若本 skill 尚未安装（从 zip 引导安装）

当用户拿来的是压缩包（`add-oil-buddy.zip`）而非已安装的 skill 时，先替他安装：

1. 用 Python 的 `zipfile` 解压到临时目录（不用交互工具）。
2. 找到解压出的 `add-oil-buddy/` 文件夹（含 `SKILL.md`），整个复制到
   `%USERPROFILE%\.workbuddy\skills\add-oil-buddy\`；目标已存在则先备份成
   `add-oil-buddy.bak` 再覆盖，不要直接删用户数据。
3. 确认目标 `SKILL.md` 存在，清理临时解压目录。
4. 若用户的消息里带了执行时间，记下来用于第 1 步；随后从本 skill 的
   `scripts/setup.py` 继续第 1~3 步。

> 面向最终用户的「一键引导 prompt」写在分发包的 `使用说明.md` 顶部，用户复制那段发给
> WorkBuddy 即可触发上述全流程。这里的实现与之保持一致。

### 第 1 步：确认执行时间

从用户话里解析时间，转成 24 小时制 `HH:MM`：

- 「默认 / 随便 / 没说」→ `12:00`
- 「每天 3 点 / 凌晨三点」→ `03:00`
- 「中午 12:30」→ `12:30`

把结果作为 `--time` 参数。若用户说「不要提醒窗 / 直接领」，加 `--no-countdown`
（不推荐——会去掉用户可推迟的缓冲）。

### 第 2 步：体检 + 依赖自愈 + 生成任务参数

运行 `scripts/setup.py`（用绝对路径，避免工作目录差异）。**默认就加 `--ensure-deps`**，
让它自动处理依赖：

```bash
"<python>" "<skill_dir>/scripts/setup.py" --ensure-deps --time HH:MM
```

`<skill_dir>` 是本 skill 的安装目录（即 `SKILL.md` 所在目录）；`<python>` 用本机任意可用
的 Python（脚本自己会找到带 Pillow 的解释器，不必是同一个）。stdout 打印一段 JSON，
诊断信息走 stderr。`--ensure-deps` 的行为：

- 探测所有候选 Python（显式 `--python` 时只探测它）；优先选带 Pillow 的。
- 若解释器存在但缺 Pillow → 自动执行 `python -m pip install Pillow`，装完重测。
- 只有完全找不到 Python 时才失败（Python 无法自带，需用户先装 3.8+ 并勾选 Add to PATH）。

先读 JSON 顶层的 `ok`：

- `ok: false` → 把 `error` 和 `hint` 原样报给用户，不要继续建任务。
  常见：`no python with Pillow`（按 hint 让用户装 Python 或 Pillow）、`bad time`。
  若 JSON 带 `auto_install` 字段，把其中的成功/失败情况也一并说明。
- `ok: true` → 记下 `automation`（含 `name` / `prompt` / `rrule` / `scheduleType` /
  `status` / `cwds`）和 `python_exe` / `script_path` / `log_path`。若 `auto_install.ok`
  为 true，顺带告诉用户「已自动安装 Pillow」。

可选：只想体检不建任务时加 `--check`。

> 用户若明确说「不要动我的环境 / 别自动装包」，去掉 `--ensure-deps`，此时缺 Pillow 会
> 直接报错，把 hint 转述给用户由他自行安装。

### 第 3 步：用 automation_update 创建定时任务（关键，只能你做）

先 `automation_update(mode="list")` 看看是否已存在同名任务（如「Buddy加油站每日领取积分」）：

- **已存在** → 用 `mode="update"` 更新它的 `rrule` / `prompt`（保留 `id`，不要重复建）。
- **不存在** → 用 `mode="create"` 新建。

参数**直接取第 2 步 JSON 里的 `automation` 对象**，逐字段填入：

| automation_update 字段 | 取值来源 |
|---|---|
| `mode` | `create` 或 `update` |
| `id` | update 时用已存在任务的 id |
| `name` | `automation.name` |
| `prompt` | `automation.prompt`（**原样使用**，里面有绝对路径和返回值说明，别改写） |
| `scheduleType` | `automation.scheduleType`（`recurring`） |
| `rrule` | `automation.rrule`（如 `FREQ=DAILY;BYHOUR=12;BYMINUTE=0`） |
| `status` | `automation.status`（`ACTIVE`） |
| `cwds` | `automation.cwds`（skill 目录） |

> 不要试图从 Python 脚本里调用 automation 或写计划任务（Windows schtasks /
> PowerShell ScheduledTask）——一律用 `automation_update` 工具，理由见下。

**为什么必须用 WorkBuddy 内置定时任务**：它触发时 WorkBuddy 客户端必然在运行，正好满足
RPA 前提；Windows 计划任务可能在客户端没开时触发，脚本找不到窗口就会失败。

### 第 4 步：报告结果

告诉用户：任务名、每天几点、是否已激活、脚本与日志路径。可提示用户手动验证：
`"<python>" "<skill_dir>/scripts/buddy_station.py" nocheck`（`nocheck` 跳过倒计时窗，
立即跑一遍完整领取，约 3~5 秒）。

## 定时任务的行为与返回值

调度到点后，agent 按 prompt 运行 `buddy_station.py claim`：先弹 5 分钟倒计时提醒窗
（可「立即领取」或推迟），随后置顶 WorkBuddy 窗口 → 点开头像菜单 → 进入 Buddy 加油站 →
按窗口矩形推算坐标点「领取」→ **采一次按钮底色像素**判定（黑底=未领取 / 灰底=已领取）→
关窗、鼠标归位。执行段约 3~5 秒，全程不注入键盘。

日志写入 `scripts/claim_log.txt`，结果含义：

- `CLAIM_OK_PIXEL` — 按钮由黑翻灰，领取成功（返回码 0）
- `ALREADY_CLAIMED` — 点前已是「今日已领取」，今天领过，属正常（返回码 7）
- `CLAIM_UNVERIFIED` — 按钮仍黑或关窗失败，结果不确定，如实报出（返回码 6）
- `FAIL xxx` — 失败（如 `FAIL no popup`），如实报出错误行

启动前脚本会做一次前置清理：若上一轮遗留了浮层卡片，先点 × 清掉，避免次日
`FAIL no popup`。判据同样是纯像素（卡片纯白 `#FFFFFF` vs 背景 `#F7F7F7` +
右下角有实心按钮），无需 UIA。

## 改时间 / 卸载

- **改时间**：重新跑第 2 步生成新 `rrule`，用 `automation_update(mode="update", id=..., rrule=...)`。
- **暂停 / 恢复**：`automation_update(mode="update", id=..., status="PAUSED"/"ACTIVE")`。
- **卸载**：`automation_update(mode="delete", id=...)`。只删定时任务，脚本文件留在 skill 目录。

## 常见问题

- **提示 `no python with Pillow`**：让用户 `pip install Pillow`，或
  `setup.py --python "C:\path\to\python.exe"` 指定解释器。
- **任务到点报 `FAIL no popup` / 找不到窗口**：确认 WorkBuddy 客户端已登录、窗口没最小化到托盘；
  可用 `buddy_station.py probe` 重新标定（活动界面改版后坐标可能变）。
- **客户端界面大改版后失准**：`buddy_station.py` 里坐标由 `geo()` 从窗口矩形推算，
  界面结构变了需重新标定 `geo()`；`probe` 会存诊断截图辅助核对。
- **不要改的东西**： automation 的 prompt 里强调「不修改脚本、不改计划任务」，保持一致。

## 文件清单

```
add-oil-buddy/
├── SKILL.md                    本说明
└── scripts/
    ├── setup.py                环境探测 + 任务参数生成（agent 据此建任务）
    ├── buddy_station.py        主 RPA：claim / nocheck / menu / probe 四个阶段
    ├── rpa_countdown.py        5 分钟倒计时提醒窗
    ├── rpa_splash.py           全屏磨砂遮罩（鼠标穿透）
    ├── splash_render.py        分层窗口贴图工具（倒计时/遮罩共用）
    └── splash_config.json      提醒窗/遮罩外观参数
```
