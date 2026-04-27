#!/usr/bin/env python3
"""国寿 E 学请求层批量刷学时。

用法：
1. 先在 bb-browser 管理的浏览器里人工登录国寿 E 学；
2. 建议停在首页、学习中心、课程详情页或培训班页；
3. 先预览：python3 /tmp/els_study_submit.py --auto --dry-run
4. 确认后跑：python3 /tmp/els_study_submit.py --auto --workers 3

原则：
- 不自动登录；只复用当前浏览器 live session。
- 自动找未完成、有剩余学时的章节。
- 跳过无学时课程；章节学完但整课仍未完成的，多半是考试阻塞，跳过不硬怼。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

STATE_PATH = Path.home() / ".els_study_submit_state.json"
LOG_PATH = Path.home() / ".els_study_submit.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def bb(*args: str, timeout: int = 60) -> Any:
    p = subprocess.run(["bb-browser", *args, "--json"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stdout or p.stderr)
    data = json.loads(p.stdout)
    if not data.get("success", True):
        raise RuntimeError(data.get("error") or p.stdout)
    return data.get("data", data)


def js_eval(expr: str, timeout: int = 60) -> Any:
    return bb("eval", expr, timeout=timeout)["result"]


def pick_logged_tab() -> dict[str, Any]:
    tabs = bb("tab", "list")["tabs"]
    prefers = (
        "courseDetail",
        "classDetail",
        "learnCenter/course",
        "learner-studentpc-fe/index",
        "learner-studentpc-fe/?redirect=",
        "learner-studentpc-fe",
    )
    for prefer in prefers:
        for t in tabs:
            url = t.get("url", "")
            if prefer in url and "e-chinalife.com" in url:
                bb("tab", str(t["index"]))
                return t
    raise RuntimeError("没找到已登录的国寿 E 学 tab；先用 bb-browser 的浏览器人工登录")


STATE_JS = r"""
(() => {
  const app = document.querySelector('#app')?.__vue_app__;
  const st = app?.config?.globalProperties?.$store?.state || JSON.parse(sessionStorage.getItem('studentpc-store') || '{}');
  const routeParams = app?.config?.globalProperties?.$route?.params || {};
  const routeQuery = app?.config?.globalProperties?.$route?.query || {};
  const href = location.href;
  const pathNums = [...href.matchAll(/\/(\d{6,})(?=\/|$|\?|#)/g)].map(m => m[1]);
  return {
    href,
    token: st.token,
    equipmentId: st.equipmentId,
    root: st.elsRootPath || st.rootPath || location.origin,
    systemActive: st.subSystem && st.subSystem.els ? `els/${st.subSystem.els} eop/${st.subSystem.eop || st.subSystem.els}` : 'els/green eop/green',
    userGroupId: st.userGroupId,
    branchTemplateId: st.branchTemplateId,
    branchName: st.branchName,
    siteId: st.siteId,
    routeParams,
    routeQuery,
    pathNums,
    storeKeys: Object.keys(st || {})
  };
})()
"""


def get_state() -> dict[str, Any]:
    state = js_eval(STATE_JS)
    if not state.get("token"):
        raise RuntimeError("当前 tab 没有 token，可能不是已登录页；请人工登录后再跑")
    if not state.get("root"):
        raise RuntimeError("当前 tab 没有 elsRootPath/rootPath")
    return state


REQ_JS_TEMPLATE = r"""
(async () => {{
  const root = {root!r};
  const token = {token!r};
  const path = {path!r};
  const method = {method!r};
  const body = {body};
  const headers = {{
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'system': 'els',
    'eplatform': 'elsWeb',
    'protocol': 'https:',
    'system-active': {system_active!r}
  }};
  const url = `${{root}}${{path}}${{path.includes('?') ? '&' : '?'}}__token=${{token}}`;
  const r = await fetch(url, {{credentials:'include', method, headers, body: method === 'GET' ? undefined : JSON.stringify(body)}});
  const text = await r.text();
  let data;
  try {{ data = JSON.parse(text); }} catch(e) {{ data = text; }}
  return {{status: r.status, url, data}};
}})()
"""


def api(state: dict[str, Any], path: str, method: str = "GET", body: Any | None = None) -> dict[str, Any]:
    expr = REQ_JS_TEMPLATE.format(
        root=state["root"],
        token=state["token"],
        path=path,
        method=method,
        body=json.dumps(body if body is not None else {}, ensure_ascii=False),
        system_active=state.get("systemActive") or "els/green eop/green",
    )
    return js_eval(expr, timeout=120)


def unwrap(resp: dict[str, Any], label: str) -> Any:
    if resp.get("status") != 200:
        raise RuntimeError(f"{label} HTTP 失败: {resp}")
    data = resp.get("data")
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        raise RuntimeError(f"{label} API 失败: {resp}")
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def parse_minutes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        # 国寿这里常见就是分钟数；若传秒也宁可保守别夸大
        return int(value)
    s = str(value)
    m = re.search(r"(\d+(?:\.\d+)?)\s*分钟", s)
    if m:
        return int(float(m.group(1)))
    m = re.search(r"(\d+(?:\.\d+)?)\s*小时", s)
    if m:
        return int(float(m.group(1)) * 60)
    m = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", s)
    if m:
        return int(float(m.group(1)))
    return 0


def truthy_finished(x: dict[str, Any]) -> bool:
    for k in ("finished", "isFinished", "finish", "isFinish"):
        if k in x:
            v = x.get(k)
            return bool(v) and str(v).lower() not in ("false", "0", "n", "no")
    status = str(x.get("status") or x.get("courseStatus") or "").upper()
    return status in ("F", "FINISH", "FINISHED", "COMPLETED", "C")


@dataclass
class CourseInfo:
    offering_id: int
    course_id: int
    offer_id: int
    name: str
    course_type: str = ""
    source: str = ""


@dataclass
class Item:
    id: int
    name: str
    tracking_type: str
    remain_minutes: int
    finished: bool


@dataclass
class Candidate:
    offering_id: int
    name: str = ""
    course_type: str = ""
    source: str = ""
    raw: dict[str, Any] | None = None


def flatten_items(xs: Any) -> Iterable[dict[str, Any]]:
    if isinstance(xs, list):
        for x in xs:
            yield from flatten_items(x)
    elif isinstance(xs, dict):
        yield xs
        for key in ("children", "childList", "courseMenuItemDtos", "rcoList", "list"):
            if isinstance(xs.get(key), list):
                yield from flatten_items(xs[key])


def extract_offering_id(x: dict[str, Any]) -> int | None:
    # 注意：学习中心列表里 id 才是 courseDetail URL 使用的 offeringCourseId；
    # offeringId 反而会让 /course/{id}/info 返回“无效课程活动”。国寿这命名，狗看了都摇头。
    keys = (
        "offeringCourseId", "id", "courseOfferingId", "offering_course_id",
        "resourceId", "offeringId", "courseId"
    )
    for k in keys:
        v = x.get(k)
        if isinstance(v, bool) or v in (None, ""):
            continue
        try:
            n = int(v)
        except Exception:
            continue
        # 课程/资源 id 通常很长；过滤分页号这种垃圾
        if n > 100000:
            return n
    return None


def extract_candidates_from_payload(payload: Any, source: str) -> list[Candidate]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for k in ("dataList", "list", "rows", "records", "courseList"):
            if isinstance(payload.get(k), list):
                rows.extend([x for x in payload[k] if isinstance(x, dict)])
        # 有些接口直接返回数组包在 data 外层字段
        if not rows and any(k in payload for k in ("offeringCourseId", "id")):
            rows.append(payload)
    elif isinstance(payload, list):
        rows.extend([x for x in payload if isinstance(x, dict)])
    out: list[Candidate] = []
    seen: set[int] = set()
    for x in rows:
        oid = extract_offering_id(x)
        if not oid or oid in seen:
            continue
        seen.add(oid)
        name = x.get("name") or x.get("courseName") or x.get("offeringName") or x.get("title") or str(oid)
        ctype = x.get("courseType") or x.get("type") or x.get("trackingType") or ""
        out.append(Candidate(oid, str(name), str(ctype), source, x))
    return out



def extract_offering_id_from_column_row(x: dict[str, Any]) -> int | None:
    obj = x.get("offering") if isinstance(x.get("offering"), dict) else x
    for k in ("offeringCourseId", "id", "courseOfferingId"):
        v = obj.get(k) if isinstance(obj, dict) else None
        if isinstance(v, bool) or v in (None, ""):
            continue
        try:
            n = int(v)
        except Exception:
            continue
        if n > 100000:
            return n
    return extract_offering_id(x)


def column_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [x for x in (payload.get("dataList") or payload.get("list") or []) if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def extract_candidates_from_column_payload(payload: Any, source: str) -> list[Candidate]:
    rows = column_rows(payload)
    out: list[Candidate] = []
    seen: set[int] = set()
    for x in rows:
        obj = x.get("offering") if isinstance(x.get("offering"), dict) else x
        object_type = str(x.get("objectType") or obj.get("objectType") or obj.get("courseType") or obj.get("type") or "")
        # 分栏里也混专栏、考试、调查、培训班；这里只收能走 courseDetail/play/save 的课。
        if object_type and object_type not in ("ONLINE", "CLASSROOM", "OFFLINE", "OUTLINE", "MOBILE", "TRAIN", "LIVE", "CMI", "VIDEO", "AUDIO"):
            continue
        oid = extract_offering_id_from_column_row(x)
        if not oid or oid in seen:
            continue
        seen.add(oid)
        name = obj.get("name") or obj.get("courseName") or obj.get("offeringName") or obj.get("title") or str(oid)
        out.append(Candidate(oid, str(name), object_type, source, x))
    return out


def extract_subcolumn_ids_from_payload(payload: Any) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for x in column_rows(payload):
        obj = x.get("offering") if isinstance(x.get("offering"), dict) else x
        object_type = str(x.get("objectType") or obj.get("objectType") or obj.get("courseType") or obj.get("type") or "")
        if object_type != "COLUMN":
            continue
        for k in ("id", "columnId", "offeringCourseId"):
            try:
                n = int(obj.get(k))
                if n > 100000 and n not in seen:
                    seen.add(n)
                    ids.append(n)
            except Exception:
                pass
    return ids


def discover_column_ids(state: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    # 财险分站首页根栏目。首页“分类专栏”实际先从这里拿一批子专栏。
    ids.add(202204061628323855461011563973)

    # 当前“首页”右侧分栏 / 专栏路由的 locationId 本身就是栏目 id。
    for v in list((state.get("routeParams") or {}).values()) + list((state.get("routeQuery") or {}).values()):
        try:
            n = int(v)
            if n > 100000:
                ids.add(n)
        except Exception:
            pass
    for n in state.get("pathNums") or []:
        try:
            ids.add(int(n))
        except Exception:
            pass

    # 首页分栏树：pcLearn/tree 返回的是左侧/分栏分类 id，可尝试作为栏目源。
    try:
        tree = unwrap(api(state, "/els/learner-student/api/pcLearn/tree", "GET", {}), "pcLearn-tree")
    except Exception as e:
        log(f"分栏树接口不可用，跳过：{e}")
        tree = []

    def walk(nodes: Any) -> None:
        if isinstance(nodes, list):
            for node in nodes:
                walk(node)
        elif isinstance(nodes, dict):
            try:
                n = int(nodes.get("id"))
                if n > 100000:
                    ids.add(n)
            except Exception:
                pass
            for key in ("children", "childList", "list"):
                if isinstance(nodes.get(key), list):
                    walk(nodes[key])

    walk(tree)

    # 分公司/机构模板，若 store 里有 userGroupId 则也拉一下。
    user_group_id = state.get("userGroupId")
    if user_group_id:
        try:
            tpl = unwrap(api(state, "/els/learner-study/api/column/companyAllTemplateList", "GET", {"userGroupId": user_group_id, "pageSize": 50}), "companyAllTemplateList")
            rows = tpl.get("dataList") if isinstance(tpl, dict) else tpl
            for x in rows or []:
                if not isinstance(x, dict):
                    continue
                for k in ("templateId", "id", "locationId"):
                    try:
                        n = int(x.get(k))
                        if n > 100000:
                            ids.add(n)
                    except Exception:
                        pass
        except Exception as e:
            log(f"机构分栏模板接口不可用，跳过：{e}")
    return sorted(ids)


def discover_column_candidates(state: dict[str, Any], max_pages: int = 10) -> list[Candidate]:
    out: list[Candidate] = []
    seen_courses: set[int] = set()
    seen_columns: set[int] = set()
    queue: list[int] = discover_column_ids(state)

    while queue:
        column_id = queue.pop(0)
        if column_id in seen_columns:
            continue
        seen_columns.add(column_id)
        for variant in ("plain", "detail"):
            empty_first = False
            variant_had_rows = False
            for page in range(1, max_pages + 1):
                if variant == "plain":
                    path = f"/els/learner-study/api/column/offeringList/{column_id}"
                    params = {"pageNum": page, "pageSize": 50}
                else:
                    path = f"/els/learner-study/api/column/{column_id}/offeringList"
                    params = {"pageNum": page, "pageSize": 50, "searchName": ""}
                try:
                    data = unwrap(api(state, path, "GET", params), f"column-{column_id}-{variant}")
                except Exception:
                    if page == 1:
                        empty_first = True
                    break

                rows = column_rows(data)
                if rows:
                    variant_had_rows = True
                for sub_id in extract_subcolumn_ids_from_payload(data):
                    if sub_id not in seen_columns and sub_id not in queue:
                        queue.append(sub_id)

                cands = extract_candidates_from_column_payload(data, f"column {column_id} {variant} p{page}")
                for c in cands:
                    if c.offering_id not in seen_courses:
                        seen_courses.add(c.offering_id)
                        out.append(c)
                total = data.get("totalCount") if isinstance(data, dict) else None
                got = len(rows)
                # 有些根栏目 totalCount=0 但 dataList 有子栏目；按 rows 是否为空判断更靠谱。
                if not got or (total and page * 50 >= int(total)):
                    if page == 1 and not got:
                        empty_first = True
                    break
            # plain/detail 往往二选一，plain 有行就不必再打 detail。
            if variant == "plain" and variant_had_rows and not empty_first:
                break
    return out


def discover_training_class_ids(state: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    href = state.get("href") or ""
    for m in re.finditer(r"/(?:classDetail|videoO|class)/[^/]*/*(\d{6,})", href):
        ids.add(int(m.group(1)))
    for v in (state.get("routeParams") or {}).values():
        try:
            n = int(v)
            if n > 100000:
                ids.add(n)
        except Exception:
            pass
    # 我的培训班列表
    endpoints = [
        ("/els/learner-student/api/pcLearn/myTrainingClass", {"pageNum": 1, "pageSize": 50}),
        ("/els/learner-student/api/pcLearn/myTrainingClass", {"pageNum": 1, "pageSize": 50, "status": 0}),
    ]
    for path, body in endpoints:
        try:
            data = unwrap(api(state, path, "GET", body), path)
        except Exception as e:
            continue
        rows = []
        if isinstance(data, dict):
            rows = data.get("dataList") or data.get("list") or []
        elif isinstance(data, list):
            rows = data
        for x in rows:
            if not isinstance(x, dict):
                continue
            for k in ("tbcId", "id", "trainingClassId"):
                try:
                    n = int(x.get(k))
                    if n > 100000:
                        ids.add(n)
                except Exception:
                    pass
    return sorted(ids)


def discover_candidates(state: dict[str, Any], max_pages: int = 10) -> list[Candidate]:
    seen: set[int] = set()
    out: list[Candidate] = []

    def add(cands: list[Candidate]) -> None:
        for c in cands:
            if c.offering_id not in seen:
                seen.add(c.offering_id)
                out.append(c)

    # 1) 学习历史/学习中心：这里最接近“我的未完成”，优先级高
    for page in range(1, max_pages + 1):
        try:
            data = unwrap(api(state, "/els/learner-student/api/learner/center/studyHistory", "GET", {"pageSize": 50, "pageNum": page}), "study-history")
        except Exception as e:
            if page == 1:
                log(f"学习历史接口不可用，跳过：{e}")
            break
        add(extract_candidates_from_payload(data, f"study-history p{page}"))
        total = data.get("totalCount") if isinstance(data, dict) else None
        got = len(data.get("dataList") or []) if isinstance(data, dict) else 0
        if not got or (total and page * 50 >= int(total)):
            break

    # 2) 学习中心课程：官方首页也这么调，会混进推荐课；后续以 menuListNew 的剩余学时再过滤
    for page in range(1, max_pages + 1):
        path = f"/els/learner-study/api/learn/center/course?pageSize=50&pageNum={page}&"
        try:
            data = unwrap(api(state, path, "POST", {"siteId": 20}), "learn-center-course")
        except Exception as e:
            if page == 1:
                log(f"学习中心课程接口不可用，跳过：{e}")
            break
        add(extract_candidates_from_payload(data, f"learn-center p{page}"))
        total = data.get("totalCount") if isinstance(data, dict) else None
        got = len(data.get("dataList") or []) if isinstance(data, dict) else 0
        if not got or (total and page * 50 >= int(total)):
            break

    # 3) 首页右侧“分栏”/专栏课程：我的课程少时，这里课时更多
    try:
        add(discover_column_candidates(state, max_pages=max_pages))
    except Exception as e:
        log(f"分栏课程扫描失败，跳过：{e}")

    # 4) 培训班内课程
    for tbc_id in discover_training_class_ids(state):
        for page in range(1, max_pages + 1):
            try:
                data = unwrap(api(state, f"/els/learner-study/api/learner/trainingClasses/{tbc_id}/allCourses", "GET", {"pageNum": page, "pageSize": 50}), f"class-{tbc_id}-allCourses")
            except Exception as e:
                if page == 1:
                    log(f"培训班 {tbc_id} 课程接口不可用，跳过：{e}")
                break
            cands = extract_candidates_from_payload(data, f"class {tbc_id} p{page}")
            add(cands)
            total = data.get("totalCount") if isinstance(data, dict) else None
            got = len(data.get("dataList") or []) if isinstance(data, dict) else 0
            if not got or (total and page * 50 >= int(total)):
                break

    # 5) 当前详情页兜底
    for n in state.get("pathNums") or []:
        oid = int(n)
        if oid > 100000 and oid not in seen:
            add([Candidate(oid, str(oid), "", "current-url", {})])

    return out


def load_course(state: dict[str, Any], offering_id: int, hint: Candidate | None = None) -> tuple[CourseInfo, list[Item], dict[str, Any]]:
    info_resp = api(state, f"/els/learner-study/api/course/{offering_id}/info")
    menu_resp = api(state, f"/els/learner-study/api/course/{offering_id}/menuListNew")
    info = unwrap(info_resp, "info") or {}
    menu = unwrap(menu_resp, "menuListNew") or {}
    ci = CourseInfo(
        offering_id=offering_id,
        course_id=int(info.get("courseId") or offering_id),
        offer_id=int(info.get("offerId") or info.get("offeringId") or offering_id),
        name=info.get("name") or info.get("courseName") or menu.get("name") or (hint.name if hint else str(offering_id)),
        course_type=str(info.get("courseType") or (hint.course_type if hint else "") or ""),
        source=hint.source if hint else "manual",
    )
    raw_items = menu.get("courseMenuItemDtos") or menu.get("rcoList") or menu.get("children") or []
    items: list[Item] = []
    for x in flatten_items(raw_items):
        if not isinstance(x, dict):
            continue
        item_id = x.get("rcoId") or x.get("id") or x.get("itemId")
        if not item_id:
            continue
        tracking = x.get("trackingType") or x.get("type") or x.get("itemType") or ""
        # 只拿真正学习资源；目录节点无 startingUrl 且无学时就会被过滤
        remain = parse_minutes(x.get("remainStudyTime"))
        if remain <= 0 and not truthy_finished(x):
            # 有些字段只给 shouldStudyTime，作为兜底；但优先 remainStudyTime
            remain = parse_minutes(x.get("shouldStudyTime") or x.get("studyTime"))
        items.append(Item(
            id=int(item_id),
            name=x.get("name") or x.get("title") or str(item_id),
            tracking_type=str(tracking),
            remain_minutes=remain,
            finished=truthy_finished(x),
        ))
    return ci, items, menu


def refresh(state: dict[str, Any], offering_id: int) -> dict[str, Any]:
    return api(state, f"/els/learner-study/api/course/play/{offering_id}/refresh", "POST", {})


def save_progress(state: dict[str, Any], ci: CourseInfo, item: Item, raw_status: str, seconds: int,
                  attempt_id: str, attempt_token: str) -> dict[str, Any]:
    payload = {
        "riskToken": state.get("equipmentId"),
        "rcoId": item.id,
        "courseId": ci.course_id,
        "clientType": "PC",
        "classroomId": ci.offer_id,
        "rawStatus": raw_status,
        "time": seconds,
        "location": seconds,
        "learnerAttemptId": attempt_id,
        "attemptToken": attempt_token,
    }
    return api(state, "/els/learner-study/api/course/play/save", "POST", payload)


def pending_items(items: list[Item]) -> list[Item]:
    return [x for x in items if not x.finished and x.remain_minutes > 0]


def has_exam_block(menu: dict[str, Any], items: list[Item]) -> bool:
    # 章节无剩余学时但课程/菜单里出现考试列表，按考试阻塞处理
    if pending_items(items):
        return False
    text = json.dumps(menu, ensure_ascii=False).lower()
    return "exam" in text or "考试" in text or "examlist" in text


def load_done_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_done_state(data: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def submit_item(state: dict[str, Any], ci: CourseInfo, item: Item, dry_run: bool = False) -> dict[str, Any]:
    seconds = max(60, item.remain_minutes * 60)
    attempt_id = str(int(time.time() * 1000))
    attempt_token = str(uuid.uuid4())
    if dry_run:
        return {"dry_run": True, "course": ci.name, "item": asdict(item), "seconds": seconds}
    r1 = save_progress(state, ci, item, "I", min(60, seconds), attempt_id, attempt_token)
    d1 = r1.get("data") if isinstance(r1, dict) else None
    if not (r1.get("status") == 200 and (not isinstance(d1, dict) or d1.get("code") in (None, 0))):
        raise RuntimeError(f"学习中提交失败: {r1}")
    r2 = save_progress(state, ci, item, "C", seconds, attempt_id, attempt_token)
    d2 = r2.get("data") if isinstance(r2, dict) else None
    if not (r2.get("status") == 200 and (not isinstance(d2, dict) or d2.get("code") in (None, 0))):
        raise RuntimeError(f"完成提交失败: {r2}")
    return {"ok": True, "course": ci.name, "item": item.name, "seconds": seconds, "save_I": d1, "save_C": d2}


def classify_candidates(state: dict[str, Any], candidates: list[Candidate], max_courses: int | None = None) -> tuple[list[tuple[CourseInfo, list[Item]]], list[dict[str, Any]]]:
    runnable: list[tuple[CourseInfo, list[Item]]] = []
    skipped: list[dict[str, Any]] = []
    for c in candidates[:max_courses or len(candidates)]:
        try:
            ci, items, menu = load_course(state, c.offering_id, c)
            pend = pending_items(items)
            if pend:
                runnable.append((ci, pend))
            else:
                reason = "考试阻塞/仅剩考试" if has_exam_block(menu, items) else "无剩余学时或已完成"
                skipped.append({"offering_id": c.offering_id, "name": ci.name, "source": c.source, "reason": reason, "items": len(items)})
        except Exception as e:
            skipped.append({"offering_id": c.offering_id, "name": c.name, "source": c.source, "reason": f"读取失败: {e}"})
    return runnable, skipped


def run_one(offering_id: int, dry_run: bool, max_items: int | None = None) -> None:
    pick_logged_tab()
    state = get_state()
    ci, items, menu_before = load_course(state, offering_id)
    pending = pending_items(items)
    if max_items:
        pending = pending[:max_items]
    print(json.dumps({
        "course": asdict(ci),
        "items": [asdict(x) for x in items],
        "pending": [asdict(x) for x in pending],
        "skip_reason": None if pending else ("考试阻塞/仅剩考试" if has_exam_block(menu_before, items) else "无剩余学时或已完成"),
    }, ensure_ascii=False, indent=2))
    if dry_run:
        print("DRY_RUN: 不提交")
        return
    for item in pending:
        log(f"提交：{ci.name} / {item.name} / {item.remain_minutes} 分钟")
        print(json.dumps(submit_item(state, ci, item, dry_run=False), ensure_ascii=False, indent=2))
        print(json.dumps(refresh(state, offering_id), ensure_ascii=False, indent=2))
    _, items_after, _ = load_course(state, offering_id)
    print("最终章节状态:")
    print(json.dumps([asdict(x) for x in items_after], ensure_ascii=False, indent=2))


def run_auto(dry_run: bool, workers: int = 3, max_courses: int | None = None, max_pages: int = 10) -> None:
    pick_logged_tab()
    state = get_state()
    candidates = discover_candidates(state, max_pages=max_pages)
    log(f"发现候选课程 {len(candidates)} 门")
    runnable, skipped = classify_candidates(state, candidates, max_courses=max_courses)
    plan = []
    for ci, items in runnable:
        for item in items:
            plan.append((ci, item))
    summary = {
        "candidates": len(candidates),
        "runnable_courses": len(runnable),
        "tasks": len(plan),
        "skipped": skipped[:80],
        "plan": [{"course": asdict(ci), "item": asdict(item)} for ci, item in plan[:200]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if dry_run:
        log("DRY_RUN: 只预览，不提交")
        return
    if not plan:
        log("没有可刷的剩余学时任务")
        return

    done_state = load_done_state()
    done_items = set(done_state.get("done_items") or [])
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def key(ci: CourseInfo, item: Item) -> str:
        return f"{ci.offering_id}:{item.id}"

    plan = [(ci, item) for ci, item in plan if key(ci, item) not in done_items]
    log(f"开始提交任务 {len(plan)} 个，工位 {workers}")

    def worker(pair: tuple[CourseInfo, Item]) -> tuple[str, dict[str, Any]]:
        ci, item = pair
        log(f"工位启动：{ci.name} / {item.name}")
        res = submit_item(state, ci, item, dry_run=False)
        # 每个 item 后刷新该课程，省得聚合状态虚着
        rr = refresh(state, ci.offering_id)
        res["refresh"] = rr.get("data") if isinstance(rr, dict) else rr
        return key(ci, item), res

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(worker, pair): pair for pair in plan}
        for fut in as_completed(futs):
            ci, item = futs[fut]
            try:
                k, res = fut.result()
                results.append(res)
                done_items.add(k)
                done_state["done_items"] = sorted(done_items)
                save_done_state(done_state)
                log(f"完成：{ci.name} / {item.name}")
            except Exception as e:
                failures.append({"course": ci.name, "item": item.name, "error": str(e)})
                log(f"失败：{ci.name} / {item.name} -> {e}")

    # 最终复验所有涉及课程
    verify = []
    for ci, _items in runnable:
        try:
            _, after, menu = load_course(state, ci.offering_id)
            verify.append({"course": ci.name, "offering_id": ci.offering_id, "pending_after": [asdict(x) for x in pending_items(after)]})
        except Exception as e:
            verify.append({"course": ci.name, "offering_id": ci.offering_id, "verify_error": str(e)})
    print(json.dumps({"results": results, "failures": failures, "verify": verify}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course-id", type=int, help="指定 offeringCourseId，课程详情 URL 最后一段")
    ap.add_argument("--auto", action="store_true", help="自动扫描未完成、有学时、无考试阻塞课程")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-items", type=int)
    ap.add_argument("--max-courses", type=int)
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--workers", type=int, default=3, help="并发工位数，默认 3")
    args = ap.parse_args()
    if args.auto:
        run_auto(args.dry_run, workers=args.workers, max_courses=args.max_courses, max_pages=args.max_pages)
    elif args.course_id:
        run_one(args.course_id, args.dry_run, args.max_items)
    else:
        ap.error("必须指定 --auto 或 --course-id")


if __name__ == "__main__":
    main()
