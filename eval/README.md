# 评测平台（自我学习用）

用官方练习裁判 `practice_v1.1` 批量跑第二阶段对局，比较不同版本的程序。设计与结论记录在 [../docs/算法设计.md](../docs/算法设计.md)。只用于赛后学习，不是参赛程序的一部分，参赛程序也不读取这里的任何文件。

运行环境：WSL / Ubuntu 22.04 + ROS 2 Humble（裁判是 Linux x86_64 可执行文件）。

## 文件

```text
eval/
  setup.sh   准备工作区：复制练习裁判，按 git 提交或当前工作区快照各个版本
  maps/      固定评测集：stage1.json 与 stage2_1..9.json
  maps.py    随机图生成（四重旋转对称），batch 每轮默认加 1 张新随机图（--random）
  exploiters.py  thief：读裁判真值，专抢目标车能拿到的资源（只针对当前弱点）
  stage1.py  第一阶段离线模拟与最优解（optimum / sim）；bench：多版本逐图配对、--reveal 上限分解（Windows 也能跑）
  pickups.py 诊断：从裁判逐 Tick 帧还原每个资源被谁、在第几 Tick 拿走
  headroom.py 诊断：每席位资源全知上限，以及“没看见 / 看见太晚 / 看见没去”的分类
  .env       真实 LLM 配置（自己填 PRACTICE_LLM_KEY，已 gitignore）
  match.py   跑一场：启动裁判 → 启动各席位程序 → 恢复比赛 → 读取最终成绩
  batch.py   批量：每张地图按席位轮换跑多场，并行执行，输出汇总和配对比较
  paired.py  逐格配对：候选批次对一个对照批次，或对多个安慰剂批次的逐格均值（--split-cap 按伤害是否封顶拆开；--cause 理由：拆出变体生效前就分叉的重跑噪声）
  play.py    看一场：交互选地图、席位、对手、速度，浏览器打开裁判页面（桌面“RMTC 第二阶段对局.bat”调用它）
```

## 用法（在 WSL 里）

```bash
source /opt/ros/humble/setup.bash
cd /mnt/e/rm/rmtc-qiuji
# 快照版本：new = 当前工作区，q1030 = freeze-1030（永久保底对手）
bash eval/setup.sh /mnt/e/rm/practice_v1.1/rmtc_practice new=WORKTREE q1030=059875d
E=~/rmtc-eval
python3 eval/batch.py --practice $E/practice --bot new=$E/versions/new --bot q1030=$E/versions/q1030 \
  --lineup new,q1030,q1030,q1030 --parallel 4 --out $E/runs/b1 --compare new,q1030
# 针对弱点：thief 抢 new 的资源；真实 LLM 加 --ai-mode real
python3 eval/batch.py --practice $E/practice --bot new=$E/versions/new \
  --lineup new,thief,q1030,q1030 --target new --out $E/runs/t1
# 两个版本在同一组随机图上配对：--maps 里 r 开头的是随机图种子，--random 0 不再加新图
python3 eval/batch.py --practice $E/practice --bot new=$E/versions/new --bot q1030=$E/versions/q1030 \
  --lineup new,q1030,q1030,q1030 --maps r101,r202,r303 --random 0 --parallel 4 --out $E/runs/b2
# 同时跑两批时给第二批不重叠的端口 / ROS 域：--first-slot 4 占用槽位 4..4+parallel-1（两批合计并行不超过 6）
# 只看已有结果
python3 eval/batch.py --practice $E/practice --out $E/runs/b1 --summary --compare new,q1030
# 逐格配对：对照批次，或若干安慰剂批次的均值
python3 eval/paired.py $E/runs/b1:new $E/runs/p1:p1 $E/runs/p3:p3 --split-cap
# 只改后期的变体与对照本身配对，按它的决策理由拆噪声
python3 eval/paired.py $E/runs/d3:d3 $E/runs/s7:s7 --cause duel
# 看一场（端口 8090、ROS 域 60，不与批测槽位冲突）
python3 eval/play.py
```

`ref` 是官方陪练车 `robot_node`，`thief` 是 exploiter。每场的裁判日志、配置、各席位决策日志（含 `candidates` 候选动作分数）都在 `runs/<批次>/m<种子>_r<轮换>/` 下。

## 限制

- 地图事件关闭（截图没有事件表）；`--ai-mode real` 时裁判代理 `.env` 里的模型，密钥不会传给参赛程序。
- 默认 100 ms/Tick（正式 500 ms）。决策耗时 p99 约 9 ms，余量足够；但 AI 请求的相对延迟比正式比赛小。
- 9 张图来自截图提取，任务统一为两个普通、一个高价值、到达 [12,12]。
- 每张地图跑 4 次席位轮换以抵消出生角差异。比较两个版本时看同场配对结果（`--compare`），样本少时置信区间会很宽。
