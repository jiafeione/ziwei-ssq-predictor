---
name: ziwei-ssq-predictor
description: Deterministic Ziwei-Doushu-inspired China Double Chromosphere (双色球) forecasting with two groups, frozen V1/V3 algorithms, online verification of prior draws, holiday-aware next-draw dates, and strict nine-issue residual windows. Use when any AI must query recent 双色球 results, advance the prediction state, calculate the next issue, reproduce a prior result, or provide baseline and residual-corrected groups.
---

# 紫微斗数双色球确定性预测

将本 skill 用于需要**联网核验上一期开奖结果并生成下一期双组号码**的任务。该方法是娱乐性、可复算的规则系统，不保证中奖，也不构成投注建议。

## 不可变身份

- 基准组：`ZW-SSQ-DETERMINISTIC-V1`
- 修正组：`ZW-SSQ-RESIDUAL9-V3-MANUAL-ASSISTED`（核心 `ZW-SSQ-RESIDUAL9-V3`）
- 配置：只使用 `scripts/zw_ssq_v1_config.json`
- 日历：只使用 `scripts/offline_calendar/zw_daily_calendar_2026_2099.jsonl`
- 红球候选域：01–33；蓝球候选域：01–16；每次必须全域评估
- 修正窗口：目标期之前连续9期，最近到最早，权重 `[9,8,7,6,5,4,3,2,1]`
- 禁止随机数、冷热号、专家号、临时改权重、永久禁号或把实际号码直接当作预测分数

## 每次联网推进工作流

1. **读取当前状态**：优先使用用户提供的最新 `user_YYYYNNN_state.json`；没有时使用 `templates/current_state/` 快照。不要重建初始状态。核对 `pending_forecast.target`、状态摘要和日历摘要。
2. **确定上一期与下一期**：上一期必须等于 `pending_forecast.target`。下一期通常是连续下一期，但必须查询或核对官方开奖安排；国庆、春节、市场休市、临时调整等可能使开奖日跳过若干天。期号连续递增，日期按实际开奖日，星期必须一致。
3. **联网查询上一期开奖结果**：优先中国福彩网 `https://www.cwl.gov.cn/`；依次回退广东福彩、四川福彩、中彩网、网易彩票。使用网页搜索/抓取工具读取页面，不使用预测文章、开机号、专家推荐或出球顺序作为数据。按 `references/source_protocol.md` 执行。
4. **至少双来源核对**：确认同一期号、开奖日期、6个红球、1个蓝球完全一致。官方来源优先；若只有单一来源或来源内容互相矛盾，停止推进并输出 `pending_verification`，不得滚动九期或生成修正组。
5. **建立输入 JSON**：
   ```json
   {
     "previous_result": {
       "issue": "2026114",
       "draw_date": "2026-10-06",
       "weekday": "星期二",
       "red_sorted": ["07","18","23","24","29","31"],
       "blue": "10"
     },
     "next_target": {
       "issue": "2026115",
       "draw_date": "2026-10-08",
       "weekday": "星期四"
     }
   }
   ```
   红球必须恰好6个、两位、01–33、升序、不重复；蓝球必须是两位01–16。
6. **核对日柱和纳音**：从冻结日历查 `next_target.draw_date`，不得由模型心算或混用其他万年历。节假日顺延后的日期必须以用户/官方开奖安排为准，再查冻结日历。
7. **运行确定性推进**：将输入、当前状态、配置、日历交给 `scripts/advance_verified.py`（参数见下文）。该程序会重新核对上一期已封存基准预测，滚动为“新输入当期 + 此前最近8期”，并生成下一期基准组和V3修正组。
8. **验证输出**：确认窗口长度为9、期号连续、红球/蓝球全域评估计数分别为33/16、状态摘要更新、算法和配置摘要未变。测试失败或摘要不一致时不要手工修文件。

## 运行命令

在 skill 目录下执行：

```bash
python3 scripts/advance_verified.py \
  --state /path/to/user_2026114_state.json \
  --input /path/to/source_2026114_fixed_input.json \
  --state-output /path/to/user_2026115_state.json \
  --forecast-output /path/to/user_2026115_forecast.json
```

若结果来自公开来源，状态记录使用 `source_verified`/`source_verified_public_web` 等明确标记，并在结果中保留来源 URL；若是用户手动输入，必须使用 `user_attested_not_source_verified`，不可声称已经联网核验。保持原项目的算法状态字段，不把联网来源标记误写成算法参数。

## 两组输出格式

始终同时输出：

- **严格紫微基准组**：`forecast.core_forecast.baseline.red`、`.blue`
- **最近9期残差修正组**：`forecast.core_forecast.residual9_corrected.red`、`.blue`

同时展示目标期、开奖日、日柱、纳音、九期窗口和数据核验状态。红球展示为升序两位字符串。末尾明确说明：结果只保证相同配置、日历、状态和输入可复算，不保证开奖或中奖。

## 复现要求

要让其他 AI 得到相同结果，必须传递或保留以下全部材料：

1. 冻结配置文件及其 `config_digest`；
2. 冻结日历文件及其 SHA-256；
3. 完整九期状态事实：期号、日期、日柱、纳音、每期基准预测和实际号码；
4. 目标期日期、日柱、纳音；
5. 算法版本、权重和状态摘要。

不要只传最终号码。跨语言实现必须按项目规范的 UTF-8、Unicode 键排序、无空白 JSON 和 SHA-256 决胜规则实现。

## 资源导航

- `references/source_protocol.md`：联网来源优先级、双源核验、节假日与失败处理。
- `references/reproducibility.md`：V1/V3 冻结规则、输入字段和状态字段。
- `scripts/advance_verified.py`：可执行状态推进封装。
- `scripts/zw_ssq_deterministic.py`、`scripts/zw_ssq_residual9_v3.py`、`scripts/offline_manual_runner.py`：冻结参考实现。
- `templates/current_state/`：随 skill 打包的当前状态快照；实际运行时应复制为工作状态并继续保存新状态。
