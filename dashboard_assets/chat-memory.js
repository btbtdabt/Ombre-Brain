(function () {
  function dailyChatMemoryApiBase() {
    return typeof BASE !== 'undefined' ? BASE : '';
  }

  function setDailyChatMemoryMessage(message, tone) {
    var el = document.getElementById('daily-chat-memory-message');
    if (!el) return;
    el.textContent = message || '';
    el.classList.remove('ok', 'error');
    if (tone) el.classList.add(tone);
  }

  async function loadDailyChatMemoryPending() {
    var target = document.getElementById('daily-chat-memory-pending');
    if (!target) return;
    target.innerHTML = '<div class="loading">读取候选...</div>';
    try {
      var res = await authFetch(dailyChatMemoryApiBase() + '/api/daily-chat-memory/pending?limit=20');
      if (!res) return;
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || '读取失败');
      target.innerHTML = renderDailyChatMemoryPending(data.items || []);
    } catch (e) {
      target.innerHTML = '<div class="loading">读取失败: ' + esc(e.message) + '</div>';
    }
  }

  function renderDailyChatMemoryPending(items) {
    if (!items.length) return '<div class="loading">暂无待确认候选。</div>';
    return items.map(function (item) {
      var candidate = item.candidate || {};
      var id = item.id || '';
      return '<div class="chat-memory-card">' +
        '<strong>' + esc(candidate.title || id) + '</strong>' +
        '<div class="chat-memory-card-body">' + esc(candidate.content || '') + '</div>' +
        '<div class="chat-memory-card-meta">' +
          esc((candidate.kind || 'memory') + ' · ' + (item.date || '') + ' · confidence ' + (candidate.confidence || '')) +
        '</div>' +
        '<div class="chat-memory-card-actions">' +
          '<button type="button" onclick="confirmDailyChatMemory(\'' + jsString(id) + '\', \'confirm\')">写入</button>' +
          '<button type="button" class="danger" onclick="confirmDailyChatMemory(\'' + jsString(id) + '\', \'reject\')">拒绝</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  async function confirmDailyChatMemory(id, action) {
    var isReject = action === 'reject';
    if (!confirm(isReject ? '拒绝这条候选？' : '写入这条长期记忆候选？')) return;
    try {
      var res = await authFetch(dailyChatMemoryApiBase() + '/api/daily-chat-memory/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_ids: [id],
          action: isReject ? 'reject' : 'confirm',
          confirm: isReject ? 'REJECT' : 'WRITE',
        }),
      });
      if (!res) return;
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || '操作失败');
      setDailyChatMemoryMessage(isReject ? '已拒绝候选。' : '已写入候选。', 'ok');
      loadDailyChatMemoryPending();
      if (!isReject) loadBuckets();
    } catch (e) {
      setDailyChatMemoryMessage('操作失败: ' + e.message, 'error');
    }
  }

  function initDailyChatMemoryTab() {
    loadDailyChatMemoryPending();
  }

  window.setDailyChatMemoryMessage = setDailyChatMemoryMessage;
  window.loadDailyChatMemoryPending = loadDailyChatMemoryPending;
  window.renderDailyChatMemoryPending = renderDailyChatMemoryPending;
  window.confirmDailyChatMemory = confirmDailyChatMemory;
  window.initDailyChatMemoryTab = initDailyChatMemoryTab;

  if (typeof getActiveTab === 'function' && getActiveTab() === 'chat-memory') {
    initDailyChatMemoryTab();
  }
})();
