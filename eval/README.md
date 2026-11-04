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
  replay.py  帧日志离线重放：候选在对照的帧上第一次动作不同的 Tick（batch.py --screen 用它跳过相同的格）
  audit.py   逐决策审计：看见时我们是所有机器人里最近、最后被对手拿走的资源（每场次数与价值，列出最差的局）
  paired.py  逐格配对：候选批次对一个对照批次，或对多个安慰剂批次的逐格均值（--split-cap 按伤害是否封顶拆开；--cause 理由：拆出变体生效前就分叉的重跑噪声；+ 合并多组图；报告 mde）
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
# 标准集 100 格（F + R1 + R2，见 docs/算法设计.md 3.0）用 + 合并成一次配对
python3 eval/paired.py $E/runs/d3:d3 $E/runs/s7:s7 + $E/runs/d3r:d3 $E/runs/s7r:s7 + $E/runs/d3x:d3 $E/runs/s7x:s7 --cause duel
# 看一场（端口 8090、ROS 域 60，不与批测槽位冲突）
python3 eval/play.py
```

### 对手池：其他队伍的公开程序

版本目录里放一个 `run_bot.sh` 就能作为对手：`match.py` 以 `run_bot.sh 席位 队名 输出目录` 启动它，ROS 域、`RMTC_AI_URL`（本场裁判网关）和 `RMTC_TEAM_TOKEN` 从环境变量传入。三个 C++ 对手在 WSL 的 `~/rmtc-eval/opponents/` 下各自独立的 colcon 工作空间里编译（它们的代码不进本仓库），nlohmann/json 装在 `opponents/deps`：

| 名字 | 来源 | 启动 |
|---|---|---|
| `ff` | github.com/faultfaust/rmtc（MIT），`match/stage2_ros2` | `ff_ws` 的 `rmtc_bot` |
| `ls` | github.com/lishirun/rmtc-bot（v12，无许可证文件，只作本地研究） | `ls_ws` 的 `contestant` |
| `mi` | github.com/Miraculy/RMTC_coding，`stage2/` 发布版 + `final.yaml` | `mi_ws` 的 `contestant` |

```bash
python3 eval/batch.py --practice $E/practice --bot base=$E/versions/base --bot ff=$E/versions/ff \
  --bot ls=$E/versions/ls --bot mi=$E/versions/mi --lineup base,ff,ls,mi --out $E/runs/pool
```

`ref` 是官方陪练车 `robot_node`，`thief` 是 exploiter。每场的裁判日志、配置、各席位决策日志（含 `candidates` 候选动作分数）都在 `runs/<批次>/m<种子>_r<轮换>/` 下。

## 限制

- 地图事件关闭（截图没有事件表）；`--ai-mode real` 时裁判代理 `.env` 里的模型，密钥不会传给参赛程序。
- 默认 250 ms/Tick（正式 500 ms）。决策中位数约 7 ms，但开局资源规划在负载下可到 170 ms；100 ms/Tick 会按负载丢动作，结果不可复现（docs/算法设计.md 3.3）。AI 请求的相对延迟比正式比赛小。
- 9 张图来自截图提取，任务统一为两个普通、一个高价值、到达 [12,12]。
- 每张地图跑 4 次席位轮换以抵消出生角差异。比较两个版本时看同场配对结果（`--compare`），样本少时置信区间会很宽。
