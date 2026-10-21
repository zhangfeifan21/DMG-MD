#!/usr/bin/env python3
"""Render inspectable O5 result tables from the recomputed JSON, without GPUs."""
from pathlib import Path
import argparse
import json
from analyze_overlap_o5 import paired_ratio,save,write_csv


def render(root):
    root=Path(root)
    def read(name,default=None):
        p=root/name
        return json.loads(p.read_text()) if p.exists() else default
    def number(x):return 'N/A' if x is None else f'{x:.4f}'
    def interval(row,key='ci95'):
        values=row.get(key)
        return 'N/A' if not values else '['+', '.join(number(v) for v in values)+']'
    ratios=read('ratios.json',[])
    trials=read('trial-analysis.json',[])
    backend=[]
    for p in (4,8):
        values={mode:{r['repeat']:r['seconds'] for r in trials if r['admission_status']=='PASS' and
                     (r['case'],r['ranks'],r['version'],r['backend'],r['mode'])==('water_400k',p,'o4',back,mode)}
                for mode,back in [('full','HostStaged'),('overlap-wait','CudaAware'),('overlap-poll','CudaAware')]}
        for index,mode in enumerate(('overlap-wait','overlap-poll')):
            backend.append(dict(case='water_400k',ranks=p,numerator='HostStaged/full',denominator='CudaAware/'+mode,
                scope='post hoc exploratory cross-backend comparison; independent adoption holdout contains CudaAware only',
                **paired_ratio(values['full'],values[mode],2026102101+p*10+index)))
    save(root/'default-backend-comparison.json',backend);write_csv(root/'default-backend-comparison.csv',backend)
    lookup={(r['case'],r['ranks'],r['backend'],r['contrast']):r for r in ratios}
    lines=['# O5 由原始数据重算的结果表','',
           '比值为中位数之比；区间以独立重复块配对 bootstrap 20,000 次。主矩阵为点态 95% 区间。',
           'P1 为 M1 full fallback；P>1 为 M2a。空 interior 不构成 overlap 证据。','',
           '| case | P | 后端 | n | 分区成本 serial/full | 增量 serial/wait | 净 full/wait [95% CI] | 净 full/poll [95% CI] | wait/poll [95% CI] |',
           '|---|---:|---|---:|---:|---:|---|---|---|']
    cells=sorted({(r['case'],r['ranks'],r['backend']) for r in ratios})
    for case,p,back in cells:
        row=lambda name:lookup[(case,p,back,name)]
        wait,poll,wp=[row(n) for n in ('net overlap-wait','net overlap-poll','poll vs wait')]
        lines.append(f'| {case} | {p} | {back} | {wait["n"]} | {number(row("partition cost")["estimate"])} | '
                     f'{number(row("increment overlap-wait")["estimate"])} | {number(wait["estimate"])} {interval(wait)} | '
                     f'{number(poll["estimate"])} {interval(poll)} | {number(wp["estimate"])} {interval(wp)} |')
    lines+=['','## 历史新增成本','',
            '每行五个独立配对块；跨二进制的整个 revision 差异不能解释为纯函数开销。慢样本全部保留。','',
            '| case | P | 后端 | 对比 | 比值 | 95% CI |','|---|---:|---|---|---:|---|']
    for r in ratios:
        if r['contrast'].startswith(('O2 ','O3 ','O4 ')):
            lines.append(f'| {r["case"]} | {r["ranks"]} | {r["backend"]} | {r["contrast"]} | {number(r["estimate"])} | {interval(r)} |')
    holdout=read('adoption-validation-analysis.json',{})
    lines+=['','## 独立采用验证','',
            f'状态 {holdout.get("status","NOT_OBSERVED")}；有效 trial {holdout.get("trials",0)}。独立样本不并入主矩阵。','',
            '| P | 模式 | n | full/overlap | 95% CI | 98.75% CI（四比较家族） | 达到冻结采用门槛 |',
            '|---:|---|---:|---:|---|---|---|']
    for r in holdout.get('contrasts',[]):
        lines.append(f'| {r["ranks"]} | {r["mode"]} | {r["n"]} | {number(r["estimate"])} | {interval(r)} | '
                     f'{interval(r,"simultaneous_ci98_75")} | {r["confirmed"]} |')
    lines+=['','## 默认后端的探索比较','',
            '采用验证固定 CudaAware；跨后端比较仅为探索，不具备独立确认样本。小于一表示 HostStaged/full 更快。','',
            '| P | 分子 | 分母 | n | 中位数之比 | 点态 95% CI |','|---:|---|---|---:|---:|---|']
    for r in backend:
        lines.append(f'| {r["ranks"]} | {r["numerator"]} | {r["denominator"]} | {r["n"]} | {number(r["estimate"])} | {interval(r)} |')
    lines+=['','## 诊断循环内 C1 完整窗口','',
            '每行是单 rank、单诊断 trial；user/system 只包含完整落在 sequence=1 loop marker 内的窗口。',
            '不是主性能正式段 CPU，不含段首 force/段末同步。进程和线程不相加，rank 峰值不相加。','',
            '| case | P | 后端 | 模式 | rank | 有效窗口 | 窗口秒数 | 覆盖率 | user CPU-s | system CPU-s | core-equivalent |',
            '|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in read('cpu-formal-loop.json',[]):
        lines.append(f'| {r["case"]} | {r["ranks"]} | {r["backend"]} | {r["mode"]} | {r["rank"]} | '
                     f'{r["valid_intervals"]} | {number(r["observed_wall_seconds"])} | {number(r["coverage"])} | '
                     f'{number(r["user_seconds"])} | {number(r["system_seconds"])} | {number(r["mean_core_equivalent"])} |')
    lines+=['','## 正式段平均 finish 回调成本','',
            '每项是各独立 trial 的单 rank 平均回调成本的中位数；poll+finish 包括 early 完成与可见性等待、unpack。',
            '不是纯 MPI/网络时间，也不是整个进程 CPU。正式段 CPU 仍为 null。','',
            '| case | P | 后端 | 模式 | rank | n | wall ms/callback | thread CPU ms/callback | progress/callback |',
            '|---|---:|---|---|---:|---:|---:|---:|---:|']
    for r in read('formal-cost-summary.json',[]):
        if r['version']!='o4' or not r['mode'].startswith('overlap-'):continue
        m=r['metrics']
        lines.append(f'| {r["case"]} | {r["ranks"]} | {r["backend"]} | {r["mode"]} | {r["rank"]} | {r["trial_n"]} | '
                     f'{number(m["mean_callback_wall_ms"]["median"])} | {number(m["mean_callback_thread_cpu_ms"]["median"])} | '
                     f'{number(m["progress_per_callback"]["median"])} |')
    lines+=['','## 强扩展','',
            'S=T(1)/T(P), E=S/P；只用匹配的重复块（追加到十重复的 P 仍只有五个可配对 P1）。',
            'P1 M1 与 P>1 M2a 的路径变化、K=4 物理核/rank 均保留。','',
            '| case | P | 后端 | 执行策略 | n | S | E | S 的 95% CI |','|---|---:|---|---|---:|---:|---:|---|']
    for r in read('scaling.json',[]):
        lines.append(f'| {r["case"]} | {r["ranks"]} | {r["backend"]} | {r["mode"]} | {r["n"]} | '
                     f'{number(r["speedup"])} | {number(r["efficiency"])} | {interval(r)} |')
    (root/'report-tables.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    render(p.parse_args().root)
