---
name: china-life-els-request-layer-study-submit
description: 中国人寿「国寿 E 学」新版 PC 端请求层刷学时流程。适用于用户已人工登录后，需要用现有浏览器会话读取未完成课程、避开考试/无学分课程，并通过 play/save 推进学习进度；不是自动登录方案。
---

# 国寿 E 学请求层学习进度提交

## 触发条件

- 用户要继续做「国寿 E 学 / 国寿E学 / 中国人寿 ELS」刷学时自动化。
- 用户已接受方案：**每次先人工登录一次，脚本复用已登录浏览器会话**。
- 目标是自动找未完成、有学时、无考试阻塞的课程，并提交学习进度。

## 核心结论

新版学习进度最终落库口是：

```http
POST https://elsbluese.e-chinalife.com/els/learner-study/api/course/play/save?__token=<token>
```

配套刷新口：

```http
POST https://elsbluese.e-chinalife.com/els/learner-study/api/course/play/<offeringCourseId>/refresh?__token=<token>
body: {}
```

`refresh` 只刷新/聚合状态，不是主提交口；主提交口是 `play/save`。

## 已实测事实

- 视频课程可通过 `play/save` 推进到 `SPprogress: 100`。
- CMI 章节也可通过同一接口推进，章节状态可从未完成变为 `finished: true`。
- 有考试条件的课程即使学习进度满了，整课仍可能显示未完成，原因是考试未通过，不是学时提交失败。
- 当前脚本原型曾放在 `/tmp/els_study_submit.py`，但 `/tmp` 不应视为长期稳定路径。

## 关键接口

### 课程信息

```http
GET /els/learner-study/api/course/<offeringCourseId>/info?__token=<token>
```

常用字段：

- `courseId`
- `offerId`：作为 `classroomId`
- `checkStudy`
- `refreshFrequency`

### 章节树 / 学时状态

```http
GET /els/learner-study/api/course/<offeringCourseId>/menuListNew?__token=<token>
```

常用字段：

- `id` / `rcoId`：章节或资源 id
- `name`
- `type`
- `trackingType`
- `startingUrl`
- `finished`
- `remainStudyTime`

老组件也会用：

```http
GET /els/learner-study/api/course/<offeringCourseId>/menuList?__token=<token>
```

### 学习进度保存

```http
POST /els/learner-study/api/course/play/save?__token=<token>
Content-Type: application/json
```

最小 payload：

```json
{
  "riskToken": "<equipmentId>",
  "rcoId": 22304816538,
  "courseId": 22304899180,
  "clientType": "PC",
  "classroomId": 22304898954,
  "rawStatus": "I",
  "time": 60,
  "location": 60,
  "learnerAttemptId": "1777298354495",
  "attemptToken": "0257da30-6637-4338-b74d-c82714498433"
}
```

字段说明：

- `riskToken`：浏览器 store 中的 `equipmentId`。
- `rcoId`：章节 / 资源 id。
- `courseId`：`info.courseId`。
- `classroomId`：`info.offerId`。
- `clientType`：固定 `PC`。
- `rawStatus`：`I` 表示学习中，`C` 表示完成。
- `time`：累计学习秒数。
- `location`：播放/学习位置秒数。
- `learnerAttemptId`：进入章节时生成的毫秒时间戳字符串。
- `attemptToken`：进入章节时生成的 UUID。

保存成功常见返回：

```json
{"msg":"操作成功","data":null,"code":0}
```

### 状态刷新

```http
POST /els/learner-study/api/course/play/<offeringCourseId>/refresh?__token=<token>
body: {}
```

之后重新读 `menuListNew` 验证 `finished`、`remainStudyTime`、`SPprogress`。

## 最小重放流程

1. 用户人工登录国寿 E 学。
2. 脚本复用已登录浏览器会话，读取：
   - `__token`
   - 请求 headers / cookies
   - `store.equipmentId` 作为 `riskToken`
3. 对目标 `offeringCourseId` 调：
   - `info`
   - `menuListNew`
4. 过滤章节：
   - 跳过 `finished == true`
   - 跳过 `remainStudyTime <= 0`
5. 生成一次 attempt：

```python
learnerAttemptId = str(int(time.time() * 1000))
attemptToken = str(uuid.uuid4())
```

6. 先发学习中：

```json
{"rawStatus":"I","time":60,"location":60}
```

7. 到应学秒数后发完成：

```json
{"rawStatus":"C","time":"<应学秒数>","location":"<应学秒数>"}
```

8. 调 `refresh`。
9. 重新读 `menuListNew` 验证章节 `finished: true` / `remainStudyTime: 0`。

## 自动找课策略

目标：自动找未完成、有学时、无考试阻塞、可刷学习进度的课程。

已落地脚本路径：

```bash
# 当前可直接用的工作副本
/home/oz/.hermes/scripts/els_study_submit.py

# skill 内归档副本
/home/oz/.hermes/skills/research/china-life-els-request-layer-study-submit/scripts/els_study_submit.py

# 临时开发副本，可能被系统清理
/tmp/els_study_submit.py
```

常用命令：

```bash
# 预览自动扫描结果，不提交
python3 /tmp/els_study_submit.py --auto --dry-run --max-pages 1

# 自动扫描并以固定三工位提交
python3 /tmp/els_study_submit.py --auto --workers 3 --max-pages 1

# 指定单课调试
python3 /tmp/els_study_submit.py --course-id <offeringCourseId> --dry-run
python3 /tmp/els_study_submit.py --course-id <offeringCourseId> --max-items 3
```

已验证自动扫描接口：

1. 优先扫学习历史：

```http
GET /els/learner-student/api/learner/center/studyHistory?__token=<token>
params: pageSize, pageNum
```

返回行里 `offeringCourseId` 是课程详情页可用 id。

2. 再扫学习中心课程：

```http
POST /els/learner-study/api/learn/center/course?pageSize=50&pageNum=<n>&__token=<token>
body: {"siteId":20}
```

注意这个接口返回行里：

- `id` 才是 `courseDetail` 与 `/course/{id}/info` 可用的 `offeringCourseId`。
- `offeringId` 是 `info.offerId/classroomId`，直接拿它调 `/course/{id}/info` 会报“无效课程活动”。这命名很坑。

4. 首页右侧“分类专栏 / 分栏”可作为补充课源：

```http
GET /els/learner-study/api/column/offeringList/<columnId>
GET /els/learner-study/api/column/<columnId>/offeringList
params: pageNum, pageSize, searchName
```

实测财险分站首页根栏目：

```text
202204061628323855461011563973
```

该根栏目会返回 `objectType: COLUMN` 的子专栏；脚本需递归子专栏，再从子专栏中提取 `objectType` 为 `ONLINE/CLASSROOM/...` 的课程。课程 id 取：

```text
offering.offeringCourseId 或 offering.id
```

已实测在“分类专栏”里可发现额外可刷课程，例如“习近平新时代中国特色社会主义思想”等短视频课。

建议规则：

1. 从学习历史、学习中心课程、培训班课程列表拿课程候选。
2. 对每个候选读 `info` 与 `menuListNew`。
3. 跳过无学分/无学时课程：
   - 没有有效章节
   - 所有章节 `remainStudyTime <= 0`
   - 或课程元数据表明无需学习时长
4. 跳过考试阻塞课程：
   - 课程要求考试且当前只是考试未通过导致整课未完成
   - 学习章节已全 `finished`，但课程状态仍未完成且原因是考试
5. 只处理存在未完成且有 `remainStudyTime > 0` 的章节。
6. 用户偏好固定三工位并发；一个课题/章节完成后补位下一门有学时课题。

脚本实测结果：

- `python3 -m py_compile /tmp/els_study_submit.py` 通过。
- `--auto --dry-run --max-pages 1` 可发现候选并生成计划。
- `--auto --workers 3 --max-pages 1` 已批量提交成功，多数章节复验 `pending_after: []`。
- 个别聚合课程刷新时 `SPprogress` 已满但 `remain_minutes` 显示仍有剩余，单课重跑指定未完成 item 后可复验到章节 `finished: true`。


## videoO / lmsapi 逆向结论

- 详情页会把状态塞入 store：
  - `startingUrl`
  - `offeringCourseId`
  - `trackingType`
  - `itemId`
  - `courseStatus`
  - `refreshFrequency`
  - `playerPath`
- `playerPath` 指向：
  - `/els/learner-player-fe/lmsapi/`
- `/videoO` 是官方父容器页，相关 chunk 曾定位到：
  - `/els/learner-studentpc-fe/js/video.8984b859.js`
  - 模块 `14c9a`
- `lmsapi/video/cmi.html` 每 10 秒向父窗口 `postMessage` 学习状态。
- `videoO` 接收后最终仍走 `play/save`。
- CMI 分支会解析类似：

```json
{"type":"updateInfo","updateInfo":"cmi.core.lesson_status=completed"}
```

并把 `lesson_status == completed` 映射成 `rawStatus = C`，最后仍走 `play/save`。CMI 可额外带：

```json
{"suspendData":"<cmi.suspend_data>"}
```

## 失败与排障

### token 过期

如果 `info` / `menuListNew` 返回：

```text
401 token auth failed, errmsg:获取GI失败,用户信息为空
```

说明会话失效。按用户偏好，不做自动重登；让用户人工重登后脚本继续。

### 整课不完成

若 `SPprogress: 100`、章节 `finished: true`，但课程 `status: I`，常见原因是考试未通过。不要误判为学时提交失败。

### 不要回到旧方案

- 不要再长期靠模型盯浏览器刷，用户嫌费钱且笨。
- 不要优先修复杂 tab 状态机；请求层已经打通。
- 不要把直接打开 content/scorm 页当成主线；它只是内容层。

## 验证标准

一次有效实测至少要确认：

1. `play/save` 返回 `code: 0`。
2. `refresh` 返回操作成功。
3. 重新读 `menuListNew` 后目标章节：
   - `finished: true`
   - `remainStudyTime: 0` 或明显减少
4. 课程聚合进度 `SPprogress` 增加或到 100。
