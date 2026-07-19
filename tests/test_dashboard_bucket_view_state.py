import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "dashboard.html"
NODE = shutil.which("node")


def _node_eval_args(script: str) -> list[str]:
    node = NODE
    assert node is not None, "Node.js is unavailable"
    return [node, "-e", script]


def _dashboard_section(start_marker: str, end_marker: str) -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def test_bucket_reload_preserves_active_filter_and_page():
    source = _dashboard_section(
        "async function loadBuckets()", "function updateStats()"
    )

    assert "buildFilters();" in source
    assert "renderBuckets(filterBuckets(allBuckets), true);" in source
    assert "renderBuckets(allBuckets);" not in source


def test_filter_rebuild_restores_active_filter_without_listener_leaks():
    source = _dashboard_section("function buildFilters()", "function filterBuckets(")

    assert "if (!domains.has(currentFilter.slice(7))) currentFilter = 'all';" in source
    assert "!visibleDomains.includes(currentDomain)" in source
    assert "visibleDomains[visibleDomains.length - 1] = currentDomain;" in source
    assert "var active = t.key === currentFilter;" in source
    assert "var active = key === currentFilter;" in source
    assert "aria-pressed" in source
    assert "filters.onclick = async function(e)" in source
    assert "filters.addEventListener('click'" not in source


def test_bucket_renderer_only_resets_page_for_an_explicit_view_change():
    source = _dashboard_section("function renderBuckets(", "function gotoBucketPage(")

    assert "function renderBuckets(buckets, preservePage)" in source
    assert "if (!preservePage) bucketPage = 1;" in source


def test_clearing_search_restores_the_active_filter_not_the_all_view():
    source = _dashboard_section(
        "document.getElementById('search-input').addEventListener",
        "async function loadBuckets()",
    )

    assert "if (q) return searchBuckets(q);" in source
    assert "return renderBuckets(filterBuckets(allBuckets));" in source
    assert "renderBuckets(allBuckets);" not in source


def test_pin_and_edit_refreshes_are_awaited():
    pin_source = _dashboard_section("async function bucketPin(", "async function bucketAnchor(")
    edit_source = _dashboard_section(
        "async function bucketSaveEdit(", "async function maybeShowOnboarding("
    )

    assert "await loadBuckets();" in pin_source
    assert "await loadBuckets();" in edit_source


def test_bucket_pager_has_first_last_and_direct_page_navigation():
    source = _dashboard_section("function _bucketPagerHtml(", "function _paintBuckets(")

    assert '<nav class="bucket-pager" aria-label="记忆桶分页">' in source
    assert "gotoBucketPage(1)" in source
    assert "gotoBucketPage(' + totalPages + ')" in source
    assert 'id="bucket-page-input" type="number"' in source
    assert 'min="1" max="' in source
    assert 'step="1"' in source
    assert "jumpToBucketPage()" in source
    assert 'role="status" aria-live="polite"' in source


def test_bucket_sort_control_keeps_enough_vertical_space_for_text():
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index(".bucket-sort-control select {")
    end = html.index("}", start)
    rule = html[start:end]

    # The later global form-control rule uses 10px vertical padding with
    # !important. This local override must win without combining that padding
    # with a fixed 32px box, which clipped the lower half of Chinese glyphs.
    assert "min-height:32px" in rule
    assert "height:auto" in rule
    assert "padding:5px 28px 5px 10px !important" in rule
    assert "line-height:1.4" in rule
    assert ";height:32px" not in rule


def test_empty_bucket_view_resets_page_and_selection_state():
    source = _dashboard_section("function _paintBuckets()", "function _localBucketMatches(")
    empty_branch = source[source.index("if (!visible.length)") : source.index("// 分页：")]

    assert "bucketPage = 1;" in empty_branch
    assert "syncBucketSelectionUi();" in empty_branch


def test_select_all_control_is_scoped_to_the_current_page():
    html = DASHBOARD.read_text(encoding="utf-8")
    source = _dashboard_section(
        "function _currentBucketPageItems(", "async function runBucketBatch("
    )

    assert "全选当前页" in html
    assert "selectAllCurrentPage(this.checked)" in html
    assert "selectAllFiltered" not in html
    assert "return visible.slice(startIdx, startIdx + BUCKETS_PER_PAGE);" in source
    assert "return _currentBucketPageItems().map(function(b)" in source
    assert "var pageIds = _currentBucketPageIds();" in source
    assert "function selectAllCurrentPage(checked)" in source
    assert "_currentBucketPageIds().forEach(function(id)" in source


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_current_page_selection_runtime_boundaries():
    selection_source = _dashboard_section(
        "function _currentBucketPageItems(", "async function runBucketBatch("
    )
    normalizer_source = _dashboard_section(
        "function _normalizeBucketPage(", "function _visibleBucketTotalPages("
    )
    script = """
var _curBuckets = [{id:'hidden', dont_surface:true}].concat(
  Array.from({length:25}, (_, i) => ({id:'b' + (i + 1), dont_surface:false}))
);
var bucketPage = 2;
var BUCKETS_PER_PAGE = 10;
var selectedBucketIds = new Set(['b1']);
var selectAllBox = {checked:false, indeterminate:false, disabled:false};
var selectedCount = {textContent:''};
var document = {
  getElementById(id) {
    if (id === 'bucket-select-all') return selectAllBox;
    if (id === 'bucket-selected-count') return selectedCount;
    return null;
  },
  querySelectorAll(selector) {
    if (selector !== '.bucket-select') return [];
    return _currentBucketPageIds().map(id => ({dataset:{id}, checked:false}));
  },
};
""" + normalizer_source + selection_source + """
selectAllCurrentPage(true);
var secondPageSelected = Array.from(selectedBucketIds).sort();
var secondPageState = [selectAllBox.checked, selectAllBox.indeterminate, selectedCount.textContent];

selectAllCurrentPage(false);
var afterSecondPageClear = Array.from(selectedBucketIds).sort();
var otherPageOnlyState = [selectAllBox.checked, selectAllBox.indeterminate];

selectedBucketIds.add('b11');
syncBucketSelectionUi();
var partialState = [selectAllBox.checked, selectAllBox.indeterminate];

selectedBucketIds.delete('b11');
bucketPage = 3;
selectAllCurrentPage(true);
var lastPageSelected = Array.from(selectedBucketIds).sort();
var lastPageState = [selectAllBox.checked, selectAllBox.indeterminate];

process.stdout.write(JSON.stringify({
  secondPageSelected,
  secondPageState,
  afterSecondPageClear,
  otherPageOnlyState,
  partialState,
  lastPageSelected,
  lastPageState,
}));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["secondPageSelected"] == [
        "b1",
        "b11",
        "b12",
        "b13",
        "b14",
        "b15",
        "b16",
        "b17",
        "b18",
        "b19",
        "b20",
    ]
    assert result["secondPageState"] == [True, False, "已选 11"]
    assert result["afterSecondPageClear"] == ["b1"]
    assert result["otherPageOnlyState"] == [False, False]
    assert result["partialState"] == [False, True]
    assert result["lastPageSelected"] == [
        "b1",
        "b21",
        "b22",
        "b23",
        "b24",
        "b25",
    ]
    assert result["lastPageState"] == [True, False]


def test_bucket_sort_is_persisted_sent_to_api_and_resets_page():
    html = DASHBOARD.read_text(encoding="utf-8")
    state_source = _dashboard_section(
        "const BASE = DASHBOARD_PATH.baseUrl", "function setDeveloperMode("
    )
    load_source = _dashboard_section("async function loadBuckets()", "function updateStats()")

    assert 'id="bucket-sort"' in html
    assert '<option value="score">综合分优先</option>' in html
    assert '<option value="created_desc">最新创建优先</option>' in html
    assert '<option value="created_asc">最早创建优先</option>' in html
    assert "['score', 'created_desc', 'created_asc']" in state_source
    assert "localStorage.getItem('ombreBucketSort')" in state_source
    assert "localStorage.setItem('ombreBucketSort', bucketSort)" in state_source
    assert "bucketPage = 1;" in state_source
    assert "await loadBuckets();" in state_source
    assert "&sort=' + encodeURIComponent(requestedSort)" in load_source
    assert "'/api/buckets/light' + listQuery" in load_source
    assert "generation !== bucketLoadGeneration" in load_source


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_bucket_loader_fetches_finite_light_pages_and_appends_on_demand():
    source = _dashboard_section(
        "function saveBucketPaginationState(", "function updateStats()"
    )
    script = r"""
var bucketLoadGeneration = 0;
var bucketSort = 'score';
var BUCKET_LIST_PAGE_LIMIT = 100;
var bucketServerTotal = 0;
var bucketNextOffset = 0;
var bucketHasMore = false;
var bucketListLoadingMore = false;
var bucketListMode = 'active';
var allBuckets = [];
function emptyBucketPaginationState() {
  return {buckets:[], total:0, nextOffset:0, hasMore:false, loaded:false, sort:''};
}
var bucketPaginationByMode = {
  active: emptyBucketPaginationState(),
  archive: emptyBucketPaginationState(),
};
var selectedBucketIds = new Set();
var currentFilter = 'all';
var bucketPage = 1;
var BASE = '/ombre';
var calls = [];
var pages = [
  {buckets:[{id:'a'}, {id:'b'}], count:3, limit:100, offset:0},
  {buckets:[{id:'c'}], count:3, limit:100, offset:2},
  {buckets:[{id:'archived'}], count:1, limit:100, offset:0},
];
var statusEl = {textContent:'', style:{}};
var moreEl = {textContent:'', style:{}, disabled:false};
var retryEl = {style:{}};
var listEl = {innerHTML:''};
var document = {
  getElementById(id) {
    return {
      'bucket-load-status': statusEl,
      'bucket-load-more': moreEl,
      'bucket-load-retry': retryEl,
      'bucket-list': listEl,
    }[id] || null;
  },
};
var window = {ObPet:null};
function esc(value) { return String(value); }
function updateStats() {}
function buildFilters() {}
function filterBuckets(items) { return items; }
function renderBuckets() {}
async function authFetch(path) {
  calls.push(path);
  var payload = pages.shift();
  return {ok:true, status:200, async json() { return payload; }};
}
""" + source + r"""
(async function run() {
  await loadBuckets();
  var first = {
    ids: allBuckets.map(item => item.id),
    status: statusEl.textContent,
    moreDisplay: moreEl.style.display,
    moreDisabled: moreEl.disabled,
  };
  await loadMoreBuckets();
  var activeIds = allBuckets.map(item => item.id);
  currentFilter = 'archived';
  await switchBucketListMode('archive');
  var archive = {
    ids: allBuckets.map(item => item.id),
    nextOffset: bucketNextOffset,
  };
  currentFilter = 'all';
  await switchBucketListMode('active');
  process.stdout.write(JSON.stringify({
    calls,
    first,
    activeIds,
    archive,
    restoredActiveIds: allBuckets.map(item => item.id),
    restoredActiveOffset: bucketNextOffset,
    finalStatus: statusEl.textContent,
    finalMoreDisplay: moreEl.style.display,
  }));
})().catch(error => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["calls"] == [
        "/ombre/api/buckets/light?include_archive=false&limit=100&offset=0&sort=score",
        "/ombre/api/buckets/light?include_archive=false&limit=100&offset=2&sort=score",
        "/ombre/api/buckets/light?include_archive=true&archive_only=true&limit=100&offset=0&sort=score",
    ]
    assert result["first"]["ids"] == ["a", "b"]
    assert "已载入 2 / 共 3 条" in result["first"]["status"]
    assert result["first"]["moreDisplay"] == "inline-flex"
    assert result["first"]["moreDisabled"] is False
    assert result["activeIds"] == ["a", "b", "c"]
    assert result["archive"] == {"ids": ["archived"], "nextOffset": 1}
    assert result["restoredActiveIds"] == ["a", "b", "c"]
    assert result["restoredActiveOffset"] == 3
    assert result["finalStatus"] == ""
    assert result["finalMoreDisplay"] == "none"


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_archive_only_pages_do_not_refilter_archived_rows_by_score_or_type():
    source = _dashboard_section(
        "function filterBuckets(buckets)",
        "// 翻页状态：",
    )
    script = r"""
var currentFilter = 'archived';
var bucketListMode = 'archive';
""" + source + r"""
var rows = [
  {id:'original-dynamic', type:'dynamic', score:0.9},
  {id:'legacy-archived', type:'archived', score:0.9},
];
var archiveOnly = filterBuckets(rows).map(item => item.id);
bucketListMode = 'active';
var legacyFallback = filterBuckets(rows).map(item => item.id);
process.stdout.write(JSON.stringify({archiveOnly, legacyFallback}));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result == {
        "archiveOnly": ["original-dynamic", "legacy-archived"],
        "legacyFallback": ["legacy-archived"],
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_bucket_search_drops_a_superseded_server_response():
    source = _dashboard_section(
        "var bucketSearchGeneration = 0;", "let detailLoadGeneration"
    )
    script = r"""
var allBuckets = [];
var rendered = [];
var pending = {};
function renderBuckets(items) { rendered.push(items.map(item => item.id)); }
function authFetch(path) {
  return new Promise(resolve => { pending[path] = resolve; });
}
""" + source + r"""
(async function run() {
  var first = searchBuckets('first');
  var second = searchBuckets('second');
  pending['/api/search?q=second']({ok:true, async json() { return [{id:'second'}]; }});
  await second;
  pending['/api/search?q=first']({ok:true, async json() { return [{id:'first'}]; }});
  await first;
  process.stdout.write(JSON.stringify(rendered));
})().catch(error => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(completed.stdout) == [["second"]]


def test_time_views_use_created_and_invalid_dates_never_render_nan():
    render_source = _dashboard_section("function _paintBuckets()", "function _localBucketMatches(")
    time_source = _dashboard_section("function parseBucketDate(", "function feelFace(")

    assert "firstValidBucketTime(b.created_epoch_ms, b.created)" in render_source
    assert "b.last_active_epoch_ms, b.last_active, b.created_epoch_ms, b.created" in render_source
    assert "'创建 ' + formatCompactBucketTime(shownTime)" in render_source
    assert "Number.isNaN(d.getTime()) ? null : d" in time_source
    assert "if (!d) return '—';" in time_source


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_filter_rebuild_keeps_selected_domain_outside_visible_top_ten():
    source = _dashboard_section("function buildFilters()", "function filterBuckets(")
    script = """
let currentFilter = 'domain:d11';
let allBuckets = Array.from({length: 12}, (_, i) => ({domain: ['d' + i]}));
function escAttr(value) { return String(value); }
function esc(value) { return String(value); }
const filterElement = {innerHTML: '', onclick: null};
const document = {getElementById() { return filterElement; }};
""" + source + """
buildFilters();
const retained = currentFilter;
const retainedButton = filterElement.innerHTML.includes('data-filter="domain:d11"');
allBuckets = allBuckets.slice(0, 11);
buildFilters();
process.stdout.write(JSON.stringify([retained, retainedButton, currentFilter]));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == ["domain:d11", True, "all"]


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_server_epoch_wins_over_browser_local_naive_time():
    source = _dashboard_section("function parseBucketDate(", "function feelFace(")
    script = source + """
const normalized = firstValidBucketTime(0, '1970-01-01T00:00:00');
const fallback = firstValidBucketTime(NaN, '2000-01-01T00:00:00Z');
process.stdout.write(JSON.stringify([normalized.getTime(), fallback.getTime()]));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [0, 946684800000]


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_page_normalizer_runtime_boundaries():
    source = _dashboard_section(
        "function _normalizeBucketPage(", "function _visibleBucketTotalPages("
    )
    script = source + """
const values = [
  _normalizeBucketPage(3, 5, 1),
  _normalizeBucketPage(0, 5, 3),
  _normalizeBucketPage(99, 5, 3),
  _normalizeBucketPage(2.9, 5, 1),
  _normalizeBucketPage('', 5, 3),
  _normalizeBucketPage('not-a-page', 5, 3),
  _normalizeBucketPage(Infinity, 5, 3),
  _normalizeBucketPage(1, 0, 5),
  _normalizeBucketPage(null, 5, 0),
];
process.stdout.write(JSON.stringify(values));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [3, 1, 5, 2, 3, 3, 3, 1, 1]
