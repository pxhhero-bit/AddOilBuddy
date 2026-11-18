# -*- coding: utf-8 -*-
"""
setup.py — Buddy 加油站自动领取 skill 的「依赖自愈 + 环境探测 + 任务参数生成」器。

职责分三层：
  1. 依赖自愈：找到带 Pillow 的 Python；若解释器存在但缺 Pillow，用 --ensure-deps
     自动 `python -m pip install Pillow`（不依赖 PATH 里的 pip）。
  2. 环境探测：确认脚本齐全、时间合法。
  3. 参数生成：输出一份完整 JSON，交给 agent 调用 automation_update 真正创建定时任务。

注意：定时任务**不能**由本脚本创建——automation 是 WorkBuddy agent 的工具，Python 调不到。
agent 读本脚本的 stdout JSON，据此建任务。诊断信息走 stderr。

用法：
    python setup.py --time 12:00                  # 默认每天 12:00
    python setup.py --time 07:30 --name "领积分"
    python setup.py --check                       # 只体检，不生成任务参数
    python setup.py --ensure-deps --time 12:00    # 缺 Pillow 时自动安装，再生成参数（推荐入口）
    python setup.py --no-countdown                # 跳过 5 分钟提醒窗（不推荐）

输出：stdout 打印一段 JSON。诊断走 stderr。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # scripts/ 目录
SKILL_DIR = os.path.dirname(HERE)
REQUIRED = ['buddy_station.py', 'rpa_countdown.py', 'rpa_splash.py',
            'splash_render.py', 'splash_config.json']
PY_VERSION_CODE = ('import sys;'
                   'assert sys.version_info >= (3,8), sys.version;'
                   'print("%d.%d" % sys.version_info[:2])')
PIL_CODE = 'import PIL; print(PIL.__version__)'


def diag(msg):
    print('[setup] ' + msg, file=sys.stderr)


def run(exe, code, timeout=60):
    """在 exe 里跑一段代码，返回 (rc, out, err)。"""
    try:
        r = subprocess.run([exe, '-c', code], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()
    except Exception as e:
        return -1, '', str(e)[:200]


def probe_python(exe):
    """探测解释器状态。

    返回 dict：
      exe / exists / state: 'ready'(版本+Pillow 都好) | 'no-pil'(缺 Pillow)
                           | 'old-python'(版本低) | 'bad'(跑不起来) | 'missing'
      version / pil / detail
    """
    info = {'exe': exe, 'exists': bool(exe and os.path.exists(exe)),
            'state': 'missing', 'version': None, 'pil': None, 'detail': ''}
    if not info['exists']:
        info['detail'] = 'not found'
        return info
    rc, out, err = run(exe, PY_VERSION_CODE)
    if rc != 0:
        info['state'] = 'bad'
        info['detail'] = (err or out).splitlines()[-1][:160] if (err or out) else 'exit %d' % rc
        return info
    info['version'] = out.splitlines()[0] if out else '?'
    rc, out, err = run(exe, PIL_CODE)
    if rc == 0:
        info['state'] = 'ready'
        info['pil'] = out.splitlines()[0] if out else '?'
        info['detail'] = 'PIL ' + str(info['pil'])
    else:
        info['state'] = 'no-pil'
        info['detail'] = 'Pillow not importable'
    return info


def candidate_exes(prefer=None):
    """按优先级列出候选解释器路径（去重）。"""
    out = []
    if prefer:
        out.append(prefer)
    out.append(sys.executable)
    # WorkBuddy 隔离 venv（开箱即用的关键：多数用户机器自带）
    out.append(os.path.expanduser(
        r'~/.workbuddy/binaries/python/envs/default/Scripts/python.exe'))
    out.append(os.path.expanduser(
        '~/.workbuddy/binaries/python/envs/default/bin/python'))
    for n in ('python.exe', 'python3.exe', 'python', 'python3'):
        p = shutil.which(n)
        if p:
            out.append(p)
    seen, uniq = set(), []
    for exe in out:
        if not exe:
            continue
        norm = os.path.normcase(os.path.abspath(exe))
        if norm in seen:
            continue
        seen.add(norm)
        uniq.append(exe)
    return uniq


def survey(prefer=None, only_prefer=False):
    """探测候选，返回 (ready_info|None, no_pil_info|None, all_infos)。

    only_prefer=True 时只探测 prefer 指定的解释器（显式 --python 时尊重用户选择，
    否则 sys.executable 有 Pillow 会抢先命中，导致--python 指定无效）。
    """
    exes = [prefer] if (only_prefer and prefer) else candidate_exes(prefer)
    infos = [probe_python(e) for e in exes]
    ready = next((i for i in infos if i['state'] == 'ready'), None)
    no_pil = next((i for i in infos if i['state'] == 'no-pil'), None)
    return ready, no_pil, infos


def ensure_pillow(exe):
    """用 python -m pip 给 exe 装 Pillow，返回 (ok, detail)。"""
    diag('正在为 %s 安装 Pillow ...' % exe)
    try:
        r = subprocess.run([exe, '-m', 'pip', 'install', 'Pillow'],
                           capture_output=True, text=True, timeout=300)
        tail = (r.stdout or '') + (r.stderr or '')
        tail = tail.strip().splitlines()
        detail = tail[-1][:200] if tail else 'exit %d' % r.returncode
        if r.returncode == 0:
            return True, detail
        return False, detail
    except Exception as e:
        return False, str(e)[:200]


def build_prompt(py_exe, script_path, log_path, stage):
    """生成 automation 任务里 agent 要执行的 prompt。"""
    timeout_ms = 1900000 if stage == 'claim' else 300000
    if stage == 'claim':
        countdown_note = (
            '脚本开头会弹出 5 分钟倒计时提醒窗（可点「立即领取」马上开始，或推迟）；'
            '因此命令超时（timeout）务必设为 %d 毫秒以上。' % timeout_ms)
        stage_note = (
            '脚本行为：置顶 WorkBuddy 窗口 → 点开头像菜单进入 Buddy 加油站 → '
            '按推算坐标点击「领取」→ 采一次像素判定按钮底色（黑=未领取 / 灰=已领取）'
            '验证结果 → 关掉浮窗并把鼠标归位。执行段约 3~5 秒，全程不接管键盘。')
    else:
        countdown_note = '此阶段不弹提醒窗，命令超时设为 %d 毫秒即可。' % timeout_ms
        stage_note = '脚本行为：跳过倒计时提醒窗，直接进入领取流程。'

    return (
        '每天在本机执行「Buddy 加油站」每日积分自动领取（无人值守 RPA 任务）。\n\n'
        '执行步骤：\n'
        '1. 用 Bash 工具运行下面这条命令。%s\n\n'
        '   "%s" "%s" %s\n\n'
        '2. %s\n'
        '3. 命令结束后，读取日志最后 20 行：\n'
        '   "%s"\n'
        '   简要报告本次结果，含义如下：\n'
        '   - CLAIM_OK_PIXEL：按钮底色由黑翻灰，领取成功\n'
        '   - ALREADY_CLAIMED：点前就是「今日已领取」（返回码 7），今天已领过，属正常\n'
        '   - CLAIM_UNVERIFIED：按钮仍黑或关窗失败，结果不确定，需原样报出\n'
        '   - FAIL xxx：失败，需原样报出错误行\n'
        '4. 不要修改脚本内容，不要改动任何计划任务设置。'
        '脚本或 Python 缺失、命令报错时如实报告，不要自行猜测或改写。'
        % (countdown_note, py_exe, script_path, stage, stage_note, log_path)
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--time', default='12:00', help='每天执行时间 HH:MM（默认 12:00）')
    ap.add_argument('--name', default='Buddy加油站每日领取积分', help='定时任务名称')
    ap.add_argument('--check', action='store_true', help='只体检，不生成任务参数')
    ap.add_argument('--ensure-deps', action='store_true',
                    help='解释器缺 Pillow 时自动 python -m pip install Pillow（推荐）')
    ap.add_argument('--no-countdown', action='store_true',
                    help='生成的任务跳过 5 分钟提醒窗（不推荐）')
    ap.add_argument('--python', default=None, help='指定 Python 解释器路径')
    args = ap.parse_args()

    # 1. 校验脚本齐全
    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        diag('缺少脚本文件：' + ', '.join(missing))
        print(json.dumps({'ok': False, 'error': 'missing files',
                          'missing': missing}, ensure_ascii=False))
        return 2

    # 2. 校验时间
    try:
        hh, mm = args.time.strip().split(':')
        hh, mm = int(hh), int(mm)
        assert 0 <= hh <= 23 and 0 <= mm <= 59
    except Exception:
        diag('时间格式无效：%r（应为 HH:MM）' % args.time)
        print(json.dumps({'ok': False, 'error': 'bad time',
                          'hint': 'use HH:MM, e.g. 12:00'}, ensure_ascii=False))
        return 2
    rrule = 'FREQ=DAILY;BYHOUR=%d;BYMINUTE=%d' % (hh, mm)

    # 3. 探测 Python（含依赖自愈）
    ready, no_pil, infos = survey(args.python, only_prefer=bool(args.python))
    auto_installed = None
    if not ready and no_pil and args.ensure_deps:
        ok, detail = ensure_pillow(no_pil['exe'])
        auto_installed = {'exe': no_pil['exe'], 'ok': ok, 'detail': detail}
        if ok:
            ready = probe_python(no_pil['exe'])
            infos = [ready if i['exe'] == no_pil['exe'] else i for i in infos]

    if not ready:
        diag('未找到带 Pillow 的 Python 解释器')
        print(json.dumps({
            'ok': False,
            'error': 'no python with Pillow',
            'tried': infos,
            'auto_install': auto_installed,
            'hint': ('已装 Python 但缺 Pillow：重跑加 --ensure-deps 自动安装；'
                     '或手动 pip install Pillow；或 --python 指定解释器。'
                     '本机若无 Python，请先安装 Python 3.8+（安装时勾选 Add to PATH）'),
        }, ensure_ascii=False, indent=2))
        return 3
    diag('使用 Python: %s (Python %s, %s)' %
         (ready['exe'], ready['version'], ready['detail']))

    script_path = os.path.join(HERE, 'buddy_station.py')
    log_path = os.path.join(HERE, 'claim_log.txt')
    stage = 'nocheck' if args.no_countdown else 'claim'

    result = {
        'ok': True,
        'python_exe': ready['exe'],
        'python_detail': ready['detail'],
        'python_version': ready['version'],
        'auto_install': auto_installed,
        'script_path': script_path,
        'log_path': log_path,
        'skill_dir': SKILL_DIR,
        'automation': {
            'name': args.name,
            'prompt': build_prompt(ready['exe'], script_path, log_path, stage),
            'scheduleType': 'recurring',
            'rrule': rrule,
            'status': 'ACTIVE',
            'cwds': SKILL_DIR,
        },
        'time': args.time,
        'countdown': (not args.no_countdown),
        'checked': [{'file': f, 'ok': True} for f in REQUIRED],
    }
    if args.check:
        result['check_only'] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
