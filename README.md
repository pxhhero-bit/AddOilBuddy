# AddOilBuddy

> 一个让 WorkBuddy 每天自动领取「Buddy 加油站」每日积分的社区工具（WorkBuddy Skill）。

![platform](https://img.shields.io/badge/platform-Windows-blue)

![python](https://img.shields.io/badge/python-3.8%2B-green)

![license](https://img.shields.io/badge/license-See%20LICENSE-lightgrey)

---

## ⚠️ 免责声明

**这是一个第三方社区工具，属于个人项目，与 WorkBuddy、腾讯公司没有任何官方关联、背书、授权或技术支持。**

- 本项目不是 WorkBuddy 的官方组件，也不代表 WorkBuddy 或腾讯的立场。
- 使用本工具产生的任何后果（包括但不限于账号风险、积分异常、客户端行为变化）由使用者自行承担。
- 本项目作者与 WorkBuddy / 腾讯无雇佣、合作或代言关系；「WorkBuddy」及相关名称商标归其各自权利人所有。
- 请自行评估并遵守你所在平台的使用条款。若相关方不希望此类自动化工具存在，请停止使用。

---

## 它解决什么问题

WorkBuddy 客户端「Buddy 加油站」的每日积分作为定时任务自动领取。  
AddOilBuddy 用一套**纯像素判据的本机 RPA 脚本**代替人工点击，并借助 WorkBuddy 内置的  
定时任务能力，把它挂到每天指定时间——到点自动打开加油站、点领取、确认结果、记日志，  
无需你守在电脑前。

**因此，本skill只是一个小功能，不适合多账号需要薅羊毛的用户。本skill只是一个简单的小玩具。**

**有能力的小伙伴甚至可以直接魔改，没有任何的限制。**

## 特性

- **纯像素判据**：靠按钮底色（黑=未领取 / 灰=已领取）和卡片白底判定状态，不做 UIA 扫描、不读按钮文字、不注入键盘。
- **坐标自适应**：所有坐标从窗口矩形实时推算，兼容不同窗口尺寸与最大化；兼容 DWM 不可见边框偏移。
- **前置自愈**：启动前检测并清掉上一轮残留的浮层卡片，避免次日 `FAIL no popup`。
- **可反应的缓冲**：执行前弹 5 分钟倒计时提醒窗，可「立即领取」或推迟；全程只在 WorkBuddy 窗口内移动鼠标，结束后归位。
- **开箱即用**：一句话引导 prompt 让 agent 自动完成装 skill、装依赖、建定时任务。
- **结果可追溯**：每次执行写入 `claim_log.txt`，领取成功 / 今日已领 / 未确认 / 失败四类结果清晰区分。

## 快速开始

把 `使用说明.md` 顶部的**引导 PROMPT**整段复制，填两个空（压缩包路径、执行时间），发给 WorkBuddy。  
剩下的——解压安装 skill、自动处理 Python/Pillow 依赖、创建定时任务——全部由 agent 完成。

> 想看完整步骤与日常管理（改时间 / 暂停 / 删除 / 手动测试），见仓库内的 `使用说明.md`。

## 工作原理

整套方案分两层，职责严格分离：

| 层              | 谁执行    | 做什么                                                              |
| -------------- | ------ | ---------------------------------------------------------------- |
| 脚本层 `scripts/` | Python | 真正执行领取的 RPA 程序 + 环境探测/参数生成器 `setup.py`；**不能**自己建定时任务             |
| 调度层            | agent  | 运行 `setup.py` 拿到参数，再用 WorkBuddy 内置 `automation_update` 创建/修改定时任务 |

**为什么用 WorkBuddy 内置定时任务**：它触发时 WorkBuddy 客户端必然在运行，正好满足 RPA 前提；  
Windows 计划任务可能在客户端没开时触发，脚本找不到窗口就会失败。

## 到点后会发生什么

1. 弹「5 分钟倒计时」提醒窗：可点 **立即领取** 马上开始，或让它自动到点执行。
2. 置顶 WorkBuddy 窗口 → 点头像菜单 → 进入 Buddy 加油站 → 点「领取」→ 采一次按钮底色像素确认 → 关窗、鼠标归位。执行段约 3~5 秒。
3. 结果写入 `%USERPROFILE%\.workbuddy\skills\add-oil-buddy\scripts\claim_log.txt`。

**日志结果含义**：

| 关键字                | 含义                             | 返回码   |
| ------------------ | ------------------------------ | ----- |
| `CLAIM_OK_PIXEL`   | 按钮由黑翻灰，领取成功 ✅                  | 0     |
| `ALREADY_CLAIMED`  | 今天已领过，正常跳过 ✅                   | 7     |
| `CLAIM_UNVERIFIED` | 没确认成功，需人工看一眼                   | 6     |
| `FAIL xxx`         | 失败（如 `FAIL no popup`），把整行发给排查者 | 1 / 4 |

## 目录结构

```
AddOilBuddy/
├── README.md                   本文件
├── 使用说明.md                  面向最终用户的一键安装与管理说明
├── LICENSE                     开源协议
└── add-oil-buddy/        可直接导入的 skill 包
    ├── SKILL.md                agent 使用的工作流与职责说明
    ├── 使用说明.md              随包分发的一份（与顶层同）
    └── scripts/
        ├── setup.py            环境探测 + 依赖自愈 + 任务参数生成
        ├── buddy_station.py    主 RPA：claim / nocheck / menu / probe 四阶段
        ├── rpa_countdown.py    5 分钟倒计时提醒窗
        ├── rpa_splash.py       全屏磨砂遮罩（鼠标穿透）
        ├── splash_render.py    分层窗口贴图工具（提醒窗/遮罩共用）
        └── splash_config.json  提醒窗 / 遮罩外观参数
```

## 前置条件与边界

- **平台**：仅 Windows（依赖 Win32 API / `PrintWindow`），macOS / Linux 不适用。
- **客户端**：需已安装并登录 WorkBuddy 桌面客户端；到点时客户端在运行。
- **依赖**：Python 3.8+ 与 Pillow（缺 Pillow 时由 `setup.py --ensure-deps` 自动安装）；`uiautomation` 可选，仅失败诊断用。
- **输入设备**：不注入键盘；只在 WorkBuddy 窗口内移动鼠标点击，执行后归位，且有 5 分钟提醒窗供反应。若你不希望接管鼠标，请不要安装。
- **界面改版**：脚本靠窗口位置推算坐标，客户端大改版后可能失准，需维护者跑 `buddy_station.py probe` 重新标定。

## 许可证

见仓库内 `LICENSE` 文件。
