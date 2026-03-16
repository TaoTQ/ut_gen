# Java UT Self-Improvement Loop

用 Claude Code Skills 自动生成单元测试，并通过 train/test split 验证 skill 的自进化效果。

> **设计原理详见 [DESIGN.md](DESIGN.md)**  — 包含架构总览、自进化机制、Gate 保护、Reliability 度量等核心设计决策。

## 前置条件

```bash
brew install openjdk maven          # Java 25 + Maven 3.9
npm install -g @anthropic-ai/claude-code   # Claude Code CLI
```

确认环境：
```bash
export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"
java -version && mvn -version && claude --version
```

---

## 接入新 Java 项目（3 步）

### Step 1 — 修改 `project.json`

```json
{
  "name": "your-project-name",
  "source_base": "path/to/src/main/java",
  "test_base":   "path/to/src/test/java",
  "maven_dir":   "path/to/maven/project",
  "maven_flags": "-Dhttps.protocols=TLSv1.2",
  "java_home":   "/opt/homebrew/opt/openjdk/bin",
  "exclude_patterns": ["*Test.java", "*$*.java", "package-info.java"]
}
```

| 字段 | 说明 |
|------|------|
| `source_base` | 要测试的 Java 源码根目录 |
| `test_base` | 生成的测试文件写入目录 |
| `maven_dir` | 包含 `pom.xml` 的 Maven 项目目录（需配置 JUnit 5 + JaCoCo） |
| `maven_flags` | 传给 `mvn` 的额外参数（如跳过 checkstyle 等） |

> `source_base` 和 `maven_dir` 可以是同一个项目，也可以分开（用独立测试 harness）。

### Step 2 — 确认 `pom.xml` 包含 JUnit 5 和 JaCoCo

```xml
<dependencies>
  <dependency>
    <groupId>org.junit.jupiter</groupId>
    <artifactId>junit-jupiter</artifactId>
    <version>5.10.1</version>
    <scope>test</scope>
  </dependency>
</dependencies>

<build><plugins>
  <plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <version>3.2.2</version>
  </plugin>
  <plugin>
    <groupId>org.jacoco</groupId>
    <artifactId>jacoco-maven-plugin</artifactId>
    <version>0.8.11</version>
    <executions>
      <execution><id>prepare-agent</id><goals><goal>prepare-agent</goal></goals></execution>
      <execution><id>report</id><phase>test</phase><goals><goal>report</goal></goals></execution>
    </executions>
  </plugin>
</plugins></build>
```

### Step 3 — 运行

```bash
python3 run_loop.py 3  # 3 轮训练 + 1 轮测试验证
# 或保持兼容：
./run_loop.sh 3
```

---

## 输出说明

```
results/
  train_iter1_coverage.json   # 第 1 轮训练覆盖率
  train_iter2_coverage.json   # 第 2 轮训练覆盖率
  train_iter3_coverage.json   # 第 3 轮训练覆盖率（最终）
  test_coverage.json          # 测试集验证覆盖率
  optimizer_iter1.json        # 第 1 轮优化提案/决策记录（含 method: llm/rules）
  optimizer_iter2.json        # 第 2 轮优化提案/决策记录
  principles/                 # 经验原则库（按项目隔离，跨项目合并读取）
    {project_name}.json        #   每个项目一个文件（gate 后蒸馏 guiding/cautionary）

datasets/
  all_classes.json            # 扫描到的全部类
  train.json                  # 训练集（驱动 skill 进化）
  test.json                   # 测试集（验证泛化能力）
  regression_subset.json      # 固定回归门禁集（每轮优化后强制评测）

.claude/skills/java-ut-generator/
  SKILL.md                    # 由 skill pack 渲染生成（不要手改）

agent/skills/
  skill_pack.json             # 单一可优化技能包（优化器主改这个）
  render_skill.py             # 将 skill pack 渲染为 SKILL.md
```

最终输出对比示例：

```
  Train(final)    line= 97.7%  branch= 98.1%  method= 88.9%
  Test            line= 95.2%  branch= 93.5%  method= 87.5%

  ✓ 泛化良好（test branch 与 train 相差 -4.6%）
```

---

## 单步操作

```bash
# 渲染技能文件（当 skill_pack.json 改动后）
python3 agent/skills/render_skill.py \
  --pack agent/skills/skill_pack.json \
  --output .claude/skills/java-ut-generator/SKILL.md

# 仅重新扫描 + 分割数据集
python3 datasets/collect.py
python3 datasets/split.py --ratio 0.7
# 或按数量切分（适合快速冒烟）
python3 datasets/split.py --train-count 8 --test-count 20 --seed 42

# 仅跑一次评测
./eval.sh
./eval.sh --subset datasets/train.json --output results/my_coverage.json
./eval.sh --subset datasets/regression_subset.json --output results/regression_gate.json

# 基于评测结果自动优化 skill pack（默认 LLM 优化，失败自动 fallback 到规则）
python3 optimizer/optimize.py \
  --report results/my_coverage.json \
  --feedback results/feedback.json \
  --skill-pack agent/skills/skill_pack.json \
  --skill-output .claude/skills/java-ut-generator/SKILL.md \
  --round 1 \
  --gate-cmd "bash ./eval.sh --subset datasets/regression_subset.json --output results/regression_gate.json" \
  --gate-report results/regression_gate.json \
  --model deepseek/deepseek-chat \
  --principles-dir results/principles \
  --project-name my-project

# 纯规则模式（跳过 LLM）
python3 optimizer/optimize.py \
  --report results/my_coverage.json \
  --feedback results/feedback.json \
  --skill-pack agent/skills/skill_pack.json \
  --skill-output .claude/skills/java-ut-generator/SKILL.md \
  --round 1 --no-llm

# gate 是硬门槛：失败会 reject + 回滚
# - gate 命令失败
# - gate 报告缺失
# - 回归集上 branch 明显下降 / compile 失败率上升 / pass rate 明显下降
# gate 决策后自动蒸馏经验原则到 principles.json（accept→guiding, reject→cautionary）
#
# 交互式手动生成测试（在 Claude Code session 中）
claude /java-ut-generator

# run_loop 按数量切分（先 collect 再 split）
# 单类并行生成（默认 PARALLEL_JOBS=2，可按机器调大/调小）
TRAIN_COUNT=8 TEST_COUNT=20 SPLIT_SEED=42 PARALLEL_JOBS=3 python3 run_loop.py 1
```

---

## 常见问题

**Q: 高复杂度类（>100 方法）生成失败/超时**
A: 系统自动将公开方法数 >20 的类拆分为 ≤15 方法一组，每组独立生成测试文件（如 `StringUtilsGroup1Test.java`）。可通过修改 `run_loop.py` 中的 `CHUNK_METHOD_THRESHOLD` 和 `CHUNK_SIZE` 调整阈值。

**Q: Maven 依赖下载失败（TLS handshake error）**
A: Java 25 需要显式指定 TLS 版本，在 `project.json` 的 `maven_flags` 中加入 `-Dhttps.protocols=TLSv1.2`。

**Q: 覆盖率一直是 0% / 和上次一样**
A: eval.sh 已使用 `mvn clean test`，若仍有问题请手动删除 `maven_dir/target/` 目录后重试。

**Q: 项目有大量类，train/test split 比例怎么调**
A: `python3 datasets/split.py --ratio 0.7 --seed 42`（默认 0.7/0.3，固定 seed 保证可复现）。

**Q: 想在现有项目上跑，但 test_base 已有测试文件**
A: 建议在 `test_base` 下新建子目录（如 `generated/`），并在 `project.json` 中指向该子目录，避免覆盖原有测试。
