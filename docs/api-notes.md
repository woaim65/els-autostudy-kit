# API Notes

These notes summarize the request-layer behavior used by `scripts/els_study_submit.py`.

## Session extraction

The script runs JavaScript in the logged-in portal tab through `bb-browser` and reads the Vue/store state:

- `token`
- `equipmentId` as the progress `riskToken`
- `elsRootPath` / `rootPath`
- subsystem information for request headers
- current route parameters and IDs

No password or credential material is stored by the script.

## Course discovery endpoints

### Learning history

```http
GET /els/learner-student/api/learner/center/studyHistory
params: pageSize, pageNum
```

Rows usually expose `offeringCourseId`, which is usable with course-detail APIs.

### Learning center course page

```http
POST /els/learner-study/api/learn/center/course?pageSize=50&pageNum=<n>&__token=<token>
body: {"siteId": 20}
```

Important field mapping:

- `id` is the usable course-detail `offeringCourseId`.
- `offeringId` is commonly the classroom/offer id and should not be used as the course-detail id.

### Training classes

```http
GET /els/learner-study/api/learner/trainingClasses/<tbcId>/allCourses
params: pageNum, pageSize
```

### Homepage/category columns

```http
GET /els/learner-study/api/column/offeringList/<columnId>
GET /els/learner-study/api/column/<columnId>/offeringList
params: pageNum, pageSize, searchName
```

Column pages can return nested entries with `objectType: COLUMN`. The script recursively scans those subcolumns and only keeps course-like objects such as `ONLINE` and `CLASSROOM`.

A field-tested root column for the observed property-insurance station is:

```text
202204061628323855461011563973
```

## Course state endpoints

```http
GET /els/learner-study/api/course/<offeringCourseId>/info
GET /els/learner-study/api/course/<offeringCourseId>/menuListNew
```

Useful fields:

- `info.courseId`
- `info.offerId` as `classroomId`
- chapter `id` / `rcoId`
- chapter `trackingType`
- chapter `finished`
- chapter `remainStudyTime`

## Progress submission

```http
POST /els/learner-study/api/course/play/save?__token=<token>
Content-Type: application/json
```

Typical payload:

```json
{
  "riskToken": "<equipmentId>",
  "rcoId": 123,
  "courseId": 456,
  "clientType": "PC",
  "classroomId": 789,
  "rawStatus": "C",
  "time": 600,
  "location": 600,
  "learnerAttemptId": "1777298354495",
  "attemptToken": "uuid"
}
```

The script usually sends:

1. `rawStatus: I` with a short initial time.
2. `rawStatus: C` with the required/remaining study seconds.

Expected success response:

```json
{"msg":"操作成功","data":null,"code":0}
```

## Refresh and verification

```http
POST /els/learner-study/api/course/play/<offeringCourseId>/refresh
body: {}
```

After refresh, the script reads `menuListNew` again and checks whether pending chapters are gone.

## Known quirks

- `refresh` can report aggregate progress that does not immediately match every chapter-level field.
- Exam-required courses may remain incomplete even after all study chapters are finished.
- Some endpoints return `code: 2` for invalid activity IDs. This usually means the wrong identifier was used, not that the session is dead.
- Session expiration commonly appears as token/auth failure. The intended fix is manual re-login, not automatic credential handling.
