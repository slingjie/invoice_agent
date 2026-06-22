const form = document.getElementById('organize-form');
const button = document.getElementById('submit-button');
const statusBox = document.getElementById('progress-status');
const panel = document.getElementById('progress-panel');
const title = document.getElementById('progress-title');
const meta = document.getElementById('progress-meta');
const count = document.getElementById('progress-count');
const rows = document.getElementById('progress-rows');
const previewPanel = document.getElementById('preview-panel');
const previewMain = document.getElementById('preview-main');
const previewSummary = document.getElementById('preview-summary');
const previewCompanyForm = document.getElementById('preview-company-form');
const previewRisks = document.getElementById('preview-risks');
const previewTripAudit = document.getElementById('preview-trip-audit');
const previewRename = document.getElementById('preview-rename-content');
const exportButton = document.getElementById('export-button');
const batchPackages = document.getElementById('batch-packages');
const flowSteps = Array.from(document.querySelectorAll('[data-flow-step]'));
const workspaceGrid = document.querySelector('.workspace-grid');
const configPanelToggle = document.getElementById('config-panel-toggle');

let pollTimer = null;
let currentTaskId = null;
let currentPackageId = null;
let currentTaskMode = 'single';
const tableColumnVisibility = {};

function toggleConfigPanel() {
  const collapsed = workspaceGrid.classList.toggle('is-config-collapsed');
  const expanded = !collapsed;
  const label = expanded ? '收起任务配置' : '展开任务配置';
  configPanelToggle.setAttribute('aria-expanded', String(expanded));
  configPanelToggle.setAttribute('aria-label', label);
  configPanelToggle.title = label;
  configPanelToggle.querySelector('span').textContent = expanded ? '‹' : '›';
}

configPanelToggle.addEventListener('click', toggleConfigPanel);

function setTaskState(state) {
  const normalized = state || 'idle';
  document.body.dataset.taskState = normalized;
  statusBox.classList.remove('status-idle', 'status-running', 'status-review', 'status-exporting', 'status-done', 'status-failed');
  statusBox.classList.add(`status-${normalized}`);
  renderStepper(normalized);
}

function renderStepper(state) {
  const steps = ['upload', 'ocr', 'review', 'export'];
  const states = {
    idle: { done: [], current: 'upload' },
    running: { done: ['upload'], current: 'ocr' },
    review: { done: ['upload', 'ocr'], current: 'review' },
    exporting: { done: ['upload', 'ocr', 'review'], current: 'export' },
    done: { done: steps, current: null },
    failed: { done: ['upload'], current: null, error: 'ocr' }
  };
  const flow = states[state] || states.idle;
  flowSteps.forEach((step) => {
    const name = step.dataset.flowStep;
    step.classList.toggle('is-done', flow.done.includes(name));
    step.classList.toggle('is-current', flow.current === name);
    step.classList.toggle('is-error', flow.error === name);
  });
}

form.addEventListener('submit', async function(event) {
  event.preventDefault();
  window.clearInterval(pollTimer);
  button.disabled = true;
  button.textContent = '整理中...';
  setTaskState('running');
  statusBox.classList.add('active');
  statusBox.innerHTML = '<span class="spinner"></span>任务已提交，正在准备...';
  panel.classList.add('active');
  previewPanel.classList.remove('active');
  exportButton.disabled = true;
  rows.innerHTML = '';
  title.textContent = '准备任务';
  meta.textContent = '';
  count.textContent = '0 / 0';
  currentPackageId = null;
  batchPackages.innerHTML = '';

  try {
    const response = await fetch('/organize', {
      method: 'POST',
      headers: {'X-Requested-With': 'fetch'},
      body: new URLSearchParams(new FormData(form))
    });
    const data = await response.json();
    if (data.error) {
      throw new Error(data.error);
    }
    currentTaskId = data.task_id;
    pollTask(data.task_id);
  } catch (error) {
    button.disabled = false;
    button.textContent = '开始整理';
    setTaskState('failed');
    statusBox.classList.add('active');
    statusBox.textContent = `整理失败：${error.message}`;
  }
});

async function pollTask(taskId) {
  async function tick() {
    const response = await fetch(`/tasks/${taskId}`);
    const task = await response.json();
    renderTask(task);
    if (task.state === 'done' || task.state === 'failed' || task.state === 'review') {
      window.clearInterval(pollTimer);
      button.disabled = false;
      button.textContent = '开始整理';
    }
  }
  await tick();
  pollTimer = window.setInterval(tick, 1000);
}

function renderTask(task) {
  currentTaskMode = task.mode || 'single';
  if (task.mode === 'batch_subfolders') {
    renderBatchTask(task);
    return;
  }
  currentPackageId = null;
  batchPackages.innerHTML = '';
  const elapsed = task.elapsed_seconds || 0;
  title.textContent = task.stage || '处理中';
  meta.textContent = `已用时 ${elapsed} 秒`;
  count.textContent = `${task.completed || 0} / ${task.total || 0}`;

  if (task.state === 'done') {
    setTaskState('done');
    statusBox.innerHTML = `整理完成。Excel：<code>${escapeHtml(task.excel_path || '')}</code>`;
    exportButton.disabled = true;
    exportButton.textContent = '已导出';
  } else if (task.state === 'review') {
    setTaskState('review');
    statusBox.textContent = '识别完成，请检查预览，确认无误后导出 Excel。';
    exportButton.disabled = !task.can_export;
    exportButton.textContent = '确认导出';
  } else if (task.state === 'failed') {
    setTaskState('failed');
    statusBox.textContent = `整理失败：${task.error || 'Unknown error'}`;
    exportButton.disabled = true;
    exportButton.textContent = '确认导出';
  } else if (task.state === 'exporting') {
    setTaskState('exporting');
    statusBox.innerHTML = '<span class="spinner"></span>正在导出 Excel...';
    exportButton.disabled = true;
    exportButton.textContent = '导出中...';
  } else {
    setTaskState('running');
    statusBox.innerHTML = `<span class="spinner"></span>${task.stage || '处理中'}，请勿关闭页面。`;
    exportButton.disabled = true;
    exportButton.textContent = '确认导出';
  }

  rows.innerHTML = (task.files || []).map((file) => {
    const cls = file.status === '已识别' ? 'badge-ok' : (file.status === '失败' || file.status === '无法识别' ? 'badge-error' : 'badge-run');
    return `<tr>
      <td>${escapeHtml(file.name || '')}</td>
      <td><span class="badge ${cls}">${escapeHtml(file.status || '')}</span></td>
      <td>${escapeHtml(file.type || '')}</td>
      <td>${escapeHtml(file.amount || '')}</td>
      <td>${escapeHtml(file.message || '')}</td>
    </tr>`;
  }).join('');
  renderPreview(task.preview || null);
}

function renderBatchTask(task) {
  const elapsed = task.elapsed_seconds || 0;
  const packages = task.packages || [];
  const failed = task.failed || packages.filter((item) => item.state === 'failed').length;
  const skipped = task.skipped || packages.filter((item) => item.state === 'skipped').length;
  title.textContent = task.stage || '批量处理中';
  meta.textContent = `已用时 ${elapsed} 秒`;
  count.textContent = `${task.completed || 0} / ${task.total || 0}`;
  rows.innerHTML = packages.map((item) => {
    const cls = item.state === 'review' || item.state === 'done' ? 'badge-ok' : (item.state === 'failed' ? 'badge-error' : 'badge-run');
    return `<tr>
      <td>${escapeHtml(item.name || '')}</td>
      <td><span class="badge ${cls}">${escapeHtml(packageStateLabel(item.state))}</span></td>
      <td>${escapeHtml(item.total || 0)} 个文件</td>
      <td>${escapeHtml(item.excel_path || '')}</td>
      <td>${escapeHtml(item.error || '')}</td>
    </tr>`;
  }).join('');

  if (!currentPackageId || !packages.some((item) => item.id === currentPackageId)) {
    const firstReviewable = packages.find((item) => item.preview && Object.keys(item.preview).length);
    currentPackageId = firstReviewable ? firstReviewable.id : null;
  }
  const currentPackage = packages.find((item) => item.id === currentPackageId) || null;
  renderBatchPackages(packages, currentPackageId, task.state);
  renderPreview(currentPackage ? currentPackage.preview : null, currentPackageId);

  if (task.state === 'failed') {
    setTaskState('failed');
    statusBox.textContent = `批量整理失败：${task.error || '没有可导出的报销包'}`;
    exportButton.disabled = true;
    exportButton.textContent = '导出全部';
  } else if (task.state === 'exporting') {
    setTaskState('exporting');
    statusBox.innerHTML = '<span class="spinner"></span>正在批量导出...';
    exportButton.disabled = true;
    exportButton.textContent = '导出中...';
  } else if (task.state === 'done') {
    setTaskState('done');
    statusBox.textContent = `批量处理完成。失败 ${failed} 个，跳过 ${skipped} 个。`;
    exportButton.disabled = true;
    exportButton.textContent = '已导出';
  } else if (task.state === 'running') {
    setTaskState('running');
    statusBox.innerHTML = `<span class="spinner"></span>${escapeHtml(task.stage || '批量识别中')}。已完成的报销包可先预览。`;
    exportButton.disabled = true;
    exportButton.textContent = '等待全部识别完成';
  } else {
    setTaskState('review');
    statusBox.textContent = `批量识别完成。失败 ${failed} 个，跳过 ${skipped} 个；可逐个导出或导出全部。`;
    exportButton.disabled = !packages.some((item) => item.can_export);
    exportButton.textContent = '导出全部可导出报销包';
  }
}

function renderBatchPackages(packages, activePackageId, taskState = 'review') {
  if (!packages.length) {
    batchPackages.innerHTML = '<p class="hint">暂无报销包。</p>';
    return;
  }
  const cards = packages.map((item) => {
    const active = item.id === activePackageId ? ' is-active' : '';
    const canOpen = item.preview && Object.keys(item.preview).length;
    const action = item.can_export && taskState !== 'running'
      ? `<button class="secondary" type="button" data-package-export="${escapeHtml(item.id)}">确认导出</button>`
      : `<span class="batch-package-note">${escapeHtml(item.excel_path || item.error || packageStateLabel(item.state))}</span>`;
    return `<article class="batch-package-card${active}">
      <button class="batch-package-open" type="button" data-package-open="${escapeHtml(item.id)}"${canOpen ? '' : ' disabled'}>
        <span>${escapeHtml(item.name || '')}</span>
        <strong>${escapeHtml(packageStateLabel(item.state))}</strong>
      </button>
      <div class="batch-package-meta">
        <span>${escapeHtml(item.total || 0)} 个文件</span>
        ${action}
      </div>
    </article>`;
  }).join('');
  batchPackages.innerHTML = `<div class="batch-package-head"><h3>报销包列表</h3><span>${packages.length} 个一级子文件夹</span></div><div class="batch-package-grid">${cards}</div>`;
}

function packageStateLabel(state) {
  return {
    queued: '排队中',
    running: '处理中',
    review: '待确认',
    exporting: '导出中',
    done: '已导出',
    failed: '失败',
    skipped: '已跳过'
  }[state] || state || '';
}

exportButton.addEventListener('click', async function() {
  if (!currentTaskId) {
    return;
  }
  exportButton.disabled = true;
  exportButton.textContent = '导出中...';
  setTaskState('exporting');
  statusBox.innerHTML = '<span class="spinner"></span>正在导出 Excel...';
  try {
    const endpoint = currentTaskMode === 'batch_subfolders' ? `/tasks/${currentTaskId}/export-all` : `/tasks/${currentTaskId}/export`;
    const response = await fetch(endpoint, { method: 'POST' });
    const data = await response.json();
    if (data.error) {
      throw new Error(data.error);
    }
    pollTask(currentTaskId);
  } catch (error) {
    setTaskState('failed');
    statusBox.textContent = `导出失败：${error.message}`;
    exportButton.disabled = false;
    exportButton.textContent = '确认导出';
  }
});

batchPackages.addEventListener('click', async function(event) {
  const openButton = event.target.closest('[data-package-open]');
  if (openButton) {
    currentPackageId = openButton.dataset.packageOpen;
    const response = await fetch(`/tasks/${currentTaskId}`);
    const task = await response.json();
    renderBatchTask(task);
    return;
  }
  const exportOne = event.target.closest('[data-package-export]');
  if (!exportOne || !currentTaskId) {
    return;
  }
  const packageId = exportOne.dataset.packageExport;
  exportOne.disabled = true;
  exportOne.textContent = '导出中...';
  try {
    const response = await fetch(`/tasks/${encodeURIComponent(currentTaskId)}/packages/${encodeURIComponent(packageId)}/export`, { method: 'POST' });
    const data = await response.json();
    if (data.error) {
      throw new Error(data.error);
    }
    pollTask(currentTaskId);
  } catch (error) {
    statusBox.textContent = `导出失败：${error.message}`;
    exportOne.disabled = false;
    exportOne.textContent = '确认导出';
  }
});

function renderPreview(preview, packageId = null) {
  if (!preview) {
    return;
  }
  currentPackageId = packageId;
  previewPanel.classList.add('active');
  if ((preview.review_cards || []).length) {
    previewMain.innerHTML = `${renderOverviewTable(preview.overview_rows || [])}${renderReviewCards(preview.review_cards || [])}`;
  } else {
    previewMain.innerHTML = renderTable(preview.main_rows || [], ['序号', '凭证日期', '凭证类别', '报销大类', '是否计入金额', '发票号码', '销方名称', '购方名称', '价税合计', '起点', '终点', '行程/住宿说明', '原文件名', '重复标记', '置信度/风险提示'], 'preview-table main-preview-table', 'main', true);
  }
  previewSummary.innerHTML = renderTable(preview.summary_rows || [], ['报销大类', '计入金额合计', '张数'], 'preview-table summary-preview-table');
  previewCompanyForm.innerHTML = renderTable(preview.company_form_rows || [], ['序号', '报销大类', '凭证日期', '人员', '销方名称', '行程/住宿说明', '金额', '张数'], 'preview-table company-form-preview-table');
  previewRisks.innerHTML = renderTable(preview.risk_rows || [], ['序号', '原文件名', '凭证类别', '重复标记', '识别状态', '风险提示'], 'preview-table risk-preview-table');
  previewTripAudit.innerHTML = renderTripAudit(preview || {});
  previewRename.innerHTML = renderTable(preview.rename_rows || [], ['序号', '原文件路径', '新文件名', '复制后路径', '是否执行'], 'preview-table rename-preview-table', 'rename');
}

function renderTripAudit(preview) {
  const model = deriveTripAuditModel(preview);
  const llm = preview.trip_audit_llm_review
    ? `<div class="trip-audit-llm"><strong>模型复核</strong><p>${escapeHtml(preview.trip_audit_llm_review)}</p></div>`
    : '';
  const detail = `<details class="trip-audit-detail-table"><summary>校对明细</summary>${renderTable(preview.trip_audit_rows || [], ['校对类别', '风险级别', '结论', '证据', '关联序号', '建议动作'], 'preview-table trip-audit-preview-table')}</details>`;
  if (!model.canRenderCalendar) {
    return `${renderTripAuditSummary(model)}${renderTripAuditRiskPanel(model)}${llm}${detail}`;
  }
  return `${renderTripAuditSummary(model)}<div class="trip-audit-layout">${renderTripAuditCalendar(model)}${renderTripAuditRiskPanel(model)}</div>${llm}${detail}`;
}

function deriveTripAuditModel(preview) {
  const rows = (preview.trip_audit_rows || []).map(normalizeTripAuditRow);
  const overviewRows = preview.overview_rows || [];
  const mainRows = (preview.main_rows || []).filter((row) => row['序号'] !== '合计总金额');
  const sequenceMap = new Map();
  [...overviewRows, ...mainRows].forEach((row) => {
    const sequence = String(row['序号'] ?? '').trim();
    if (!sequence || sequence === '合计总金额' || sequenceMap.has(sequence)) {
      return;
    }
    sequenceMap.set(sequence, row);
  });
  const tripDates = deriveTripDates(mainRows);
  const days = buildTripDays(tripDates.start, tripDates.end);
  const dayMap = new Map(days.map((day) => [day.iso, { ...day, records: [], risks: [] }]));
  sequenceMap.forEach((row) => {
    const day = normalizeDateValue(row['日期'] || row['凭证日期']);
    if (!dayMap.has(day)) {
      return;
    }
    dayMap.get(day).records.push(row);
  });
  const unfiledRisks = [];
  rows.forEach((risk) => {
    const daysForRisk = riskDates(risk, sequenceMap).filter((day) => dayMap.has(day));
    if (!daysForRisk.length) {
      unfiledRisks.push(risk);
      return;
    }
    daysForRisk.forEach((day) => dayMap.get(day).risks.push(risk));
  });
  return {
    canRenderCalendar: Boolean(tripDates.start && tripDates.end && days.length && days.length <= 45),
    days: Array.from(dayMap.values()),
    start: tripDates.start,
    end: tripDates.end,
    riskRows: rows,
    unfiledRisks,
  };
}

function renderTripAuditSummary(model) {
  const riskDates = model.days.filter((day) => day.risks.length).length;
  const tripDays = model.days.length || 0;
  const dateLabel = model.start && model.end ? `${shortDate(model.start)} - ${shortDate(model.end)}` : '日期待确认';
  return `<div class="trip-audit-summary">
    <div><span>出差区间</span><strong>${escapeHtml(dateLabel)}</strong></div>
    <div><span>出差天数</span><strong>${tripDays || '-'}</strong></div>
    <div><span>风险项</span><strong>${model.riskRows.length}</strong></div>
    <div><span>涉及日期</span><strong>${riskDates}</strong></div>
  </div>`;
}

function renderTripAuditCalendar(model) {
  const paddedDays = padCalendarDays(model.days);
  const weekHead = ['一', '二', '三', '四', '五', '六', '日'].map((day) => `<div>${day}</div>`).join('');
  const cells = paddedDays.map((day) => renderTripAuditDay(day)).join('');
  return `<section class="trip-audit-calendar-wrap" aria-label="行程校对日历"><div class="trip-audit-week-head">${weekHead}</div><div class="trip-audit-calendar">${cells}</div></section>`;
}

function renderTripAuditDay(day) {
  if (day.isPadding) {
    return `<div class="trip-audit-day is-padding"><div class="trip-audit-day-date">${escapeHtml(shortDate(day.iso))}</div></div>`;
  }
  const severityClass = day.risks.some((risk) => risk.severity === 'error') ? 'is-error' : (day.risks.length ? 'is-warning' : '');
  const records = day.records.slice(0, 3).map((record) => renderTripAuditRecord(record)).join('');
  const overflow = day.records.length > 3 ? `<div class="trip-audit-more">另 ${day.records.length - 3} 条票据</div>` : '';
  const risks = day.risks.slice(0, 2).map((risk) => `<div class="trip-audit-day-risk">${escapeHtml(risk.conclusion)}</div>`).join('');
  const riskTag = day.risks.length ? `<span class="trip-audit-tag">${day.risks.length} 项风险</span>` : '';
  return `<div class="trip-audit-day ${severityClass}">
    <div class="trip-audit-day-date"><strong>${escapeHtml(shortDate(day.iso))}</strong>${riskTag}</div>
    ${records || '<div class="trip-audit-empty">暂无票据</div>'}
    ${overflow}
    ${risks}
  </div>`;
}

function renderTripAuditRecord(record) {
  const type = record['凭证类别'] || record['报销大类'] || '票据';
  const amount = record['金额'] ?? record['价税合计'] ?? '';
  const sequence = record['序号'] ?? '';
  return `<div class="trip-audit-record"><strong>#${escapeHtml(sequence)} ${escapeHtml(type)}</strong><span>${escapeHtml(amount)}</span></div>`;
}

function renderTripAuditRiskPanel(model) {
  const primaryRisks = model.riskRows.slice(0, 8).map((risk) => renderTripAuditRisk(risk)).join('');
  const unfiled = model.unfiledRisks.length
    ? `<article class="trip-audit-risk is-muted"><h4>未归档风险</h4><p>${model.unfiledRisks.map((risk) => escapeHtml(risk.conclusion)).join('；')}</p></article>`
    : '';
  if (!primaryRisks && !unfiled) {
    return '<aside class="trip-audit-risk-panel"><article class="trip-audit-risk is-ok"><h4>未发现行程校对风险</h4><p>当前票据与行程规则未发现明显冲突。</p></article></aside>';
  }
  return `<aside class="trip-audit-risk-panel">${primaryRisks}${unfiled}</aside>`;
}

function renderTripAuditRisk(risk) {
  return `<article class="trip-audit-risk ${risk.severity === 'error' ? 'is-error' : 'is-warning'}">
    <h4>${escapeHtml(risk.category)}</h4>
    <p>${escapeHtml(risk.conclusion)}</p>
    <div class="trip-audit-risk-meta">
      <span>${escapeHtml(risk.severity)}</span>
      ${risk.relatedSequences ? `<span>关联 ${escapeHtml(risk.relatedSequences)}</span>` : ''}
    </div>
  </article>`;
}

function normalizeTripAuditRow(row) {
  return {
    category: row['校对类别'] || '',
    severity: row['风险级别'] || 'info',
    conclusion: row['结论'] || '',
    evidence: row['证据'] || '',
    relatedSequences: row['关联序号'] || '',
    suggestedAction: row['建议动作'] || '',
  };
}

function deriveTripDates(rows) {
  for (const row of rows) {
    const start = normalizeDateValue(row['出差开始日期']);
    const end = normalizeDateValue(row['出差结束日期']);
    if (start && end) {
      return { start, end };
    }
  }
  return { start: '', end: '' };
}

function buildTripDays(start, end) {
  const startDate = parseIsoDate(start);
  const endDate = parseIsoDate(end);
  if (!startDate || !endDate || endDate < startDate) {
    return [];
  }
  const days = [];
  const cursor = new Date(startDate);
  while (cursor <= endDate && days.length <= 45) {
    const iso = formatIsoDate(cursor);
    days.push({ iso });
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function padCalendarDays(days) {
  if (!days.length) {
    return [];
  }
  const padded = [];
  const first = parseIsoDate(days[0].iso);
  const leading = (first.getDay() + 6) % 7;
  for (let index = leading; index > 0; index -= 1) {
    const date = new Date(first);
    date.setDate(first.getDate() - index);
    padded.push({ iso: formatIsoDate(date), isPadding: true });
  }
  padded.push(...days);
  const trailing = (7 - (padded.length % 7)) % 7;
  const last = parseIsoDate(days[days.length - 1].iso);
  for (let index = 1; index <= trailing; index += 1) {
    const date = new Date(last);
    date.setDate(last.getDate() + index);
    padded.push({ iso: formatIsoDate(date), isPadding: true });
  }
  return padded;
}

function riskDates(risk, sequenceMap) {
  const dates = new Set();
  String(risk.relatedSequences || '').split(',').map((part) => part.trim()).filter(Boolean).forEach((sequence) => {
    const row = sequenceMap.get(sequence);
    const date = row ? normalizeDateValue(row['日期'] || row['凭证日期']) : '';
    if (date) {
      dates.add(date);
    }
  });
  const datePattern = /20\d{2}[/-]\d{1,2}[/-]\d{1,2}/g;
  [risk.conclusion, risk.evidence, risk.suggestedAction].forEach((text) => {
    String(text || '').match(datePattern)?.forEach((date) => dates.add(normalizeDateValue(date)));
  });
  return Array.from(dates);
}

function normalizeDateValue(value) {
  const match = String(value || '').match(/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})/);
  if (!match) {
    return '';
  }
  return `${match[1]}-${String(Number(match[2])).padStart(2, '0')}-${String(Number(match[3])).padStart(2, '0')}`;
}

function parseIsoDate(value) {
  const normalized = normalizeDateValue(value);
  if (!normalized) {
    return null;
  }
  const [year, month, day] = normalized.split('-').map(Number);
  return new Date(year, month - 1, day);
}

function formatIsoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function shortDate(value) {
  const normalized = normalizeDateValue(value);
  return normalized ? normalized.slice(5).replace('-', '/') : value;
}

function renderOverviewTable(items) {
  if (!items.length) {
    return '<p class="hint">暂无表格总览</p>';
  }
  const columns = ['序号', '日期', '凭证类别', '报销大类', '金额', '是否计入', '风险', '原文件名'];
  const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('');
  const body = items.map((item) => {
    const sequence = item['序号'] ?? '';
    return `<tr class="overview-row" tabindex="0" data-review-target="${escapeHtml(sequence)}">${columns.map((column) => `<td>${escapeHtml(item[column] ?? '')}</td>`).join('')}</tr>`;
  }).join('');
  return `<section class="overview-section" aria-label="表格总览"><div class="preview-subhead"><h4>表格总览</h4><span>点击行定位到图文核对卡片</span></div><div class="table-frame"><table class="preview-table overview-preview-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></section>`;
}

function renderReviewCards(items) {
  const cards = items.map((item) => {
    const sequence = item['序号'] ?? '';
    const fileName = item['原文件名'] || '';
    const baseUrl = currentPackageId
      ? `/tasks/${encodeURIComponent(currentTaskId)}/packages/${encodeURIComponent(currentPackageId)}`
      : `/tasks/${encodeURIComponent(currentTaskId)}`;
    const previewUrl = currentTaskId ? `${baseUrl}/previews/${encodeURIComponent(sequence)}` : '';
    const fileUrl = currentTaskId ? `${baseUrl}/files/${encodeURIComponent(sequence)}` : '';
    const isPdf = /\.pdf$/i.test(fileName);
    const isImage = /\.(png|jpe?g|webp)$/i.test(fileName);
    const preview = renderFilePreview(previewUrl, fileUrl, fileName, isPdf, isImage);
    const readOnly = renderReviewReadOnly(item);
    const editForm = renderReviewEditForm(sequence, item);
    return `<article class="review-card" id="review-card-${escapeHtml(sequence)}" data-review-sequence="${escapeHtml(sequence)}">
      <div class="review-card-preview">${preview}</div>
      <div class="review-card-content">
        <div class="review-card-head">
          <div><span>序号 ${escapeHtml(sequence)}</span><h4>${escapeHtml(fileName)}</h4></div>
          <span class="review-include ${item['是否计入金额'] === '是' ? 'is-included' : ''}">${escapeHtml(item['是否计入金额'] || '否')}</span>
        </div>
        ${readOnly}
        ${editForm}
      </div>
    </article>`;
  }).join('');
  return `<section class="review-card-list" aria-label="图文核对"><div class="preview-subhead"><h4>图文核对</h4><span>点击发票预览可放大检查</span></div>${cards}</section>`;
}

function renderReviewReadOnly(item) {
  const summary = [
    ['项目', item['项目名称']],
    ['金额', item['价税合计']],
    ['日期', item['凭证日期']],
    ['大类', item['报销大类']]
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? '')}</strong></div>`).join('');
  const coreFields = [
    ['项目名称', item['项目名称']],
    ['凭证日期', item['凭证日期']],
    ['凭证类别', item['凭证类别']],
    ['报销大类', item['报销大类']],
    ['发票号码', item['发票号码']]
  ].map(([label, value]) => `<div class="review-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? '')}</strong></div>`).join('');
  const extraFields = [
    ['销方名称', item['销方名称']],
    ['购方名称', item['购方名称']],
    ['起点', item['起点']],
    ['终点', item['终点']],
    ['行程/住宿说明', item['行程/住宿说明']],
    ['风险提示', item['风险提示']]
  ].map(([label, value]) => `<div class="review-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? '')}</strong></div>`).join('');
  return `<div class="review-readonly">
    <div class="review-summary-strip">${summary}</div>
    <div class="review-field-grid review-core-grid">${coreFields}</div>
    <details class="review-extra-details">
      <summary>更多信息</summary>
      <div class="review-field-grid">${extraFields}</div>
    </details>
    <div class="review-edit-actions">
      <button class="review-edit-toggle-button" type="button" data-review-edit-toggle>编辑</button>
    </div>
  </div>`;
}

function renderReviewEditForm(sequence, item) {
  const coreFields = [
    ['project_name', '项目名称', item['项目名称']],
    ['total_with_tax', '价税合计', item['价税合计']],
    ['document_date', '凭证日期', item['凭证日期']],
    ['document_type', '凭证类别', item['凭证类别']],
    ['reimbursement_category', '报销大类', item['报销大类']],
    ['invoice_number', '发票号码', item['发票号码']]
  ].map(([name, label, value]) => renderEditField(name, label, value)).join('');
  const extraFields = [
    ['seller_name', '销方名称', item['销方名称']],
    ['buyer_name', '购方名称', item['购方名称']],
    ['origin', '起点', item['起点']],
    ['destination', '终点', item['终点']],
    ['description', '行程/住宿说明', item['行程/住宿说明']],
    ['risk_note', '风险提示', item['风险提示']]
  ].map(([name, label, value]) => renderEditField(name, label, value)).join('');
  return `<form class="review-edit-form" data-review-edit="${escapeHtml(sequence)}">
    <div class="review-field-grid review-core-grid">
      ${coreFields}
      <label class="review-edit-field">
        <span>是否计入金额</span>
        <select name="include_in_amount">
          <option value="是"${item['是否计入金额'] === '是' ? ' selected' : ''}>是</option>
          <option value="否"${item['是否计入金额'] !== '是' ? ' selected' : ''}>否</option>
        </select>
      </label>
    </div>
    <details class="review-edit-extra">
      <summary>更多字段</summary>
      <div class="review-field-grid">${extraFields}</div>
    </details>
    <div class="review-edit-actions">
      <span class="review-save-status" aria-live="polite"></span>
      <button class="review-cancel-button" type="button" data-review-edit-cancel>取消</button>
      <button class="review-save-button" type="submit">保存修改</button>
    </div>
  </form>`;
}

function renderEditField(name, label, value) {
  return `<label class="review-edit-field">
    <span>${escapeHtml(label)}</span>
    <input name="${escapeHtml(name)}" value="${escapeHtml(value ?? '')}">
  </label>`;
}

function renderFilePreview(previewUrl, fileUrl, fileName, isPdf, isImage) {
  if (!previewUrl || (!isPdf && !isImage)) {
    return `<div class="preview-fallback"><strong>${escapeHtml(fileName)}</strong><span>该文件类型暂不支持内嵌预览</span></div>`;
  }
  const media = `<img src="${escapeHtml(previewUrl)}" alt="${escapeHtml(fileName)}" loading="lazy">`;
  return `<div class="review-preview-button" role="button" tabindex="0" data-preview-url="${escapeHtml(previewUrl)}" data-file-url="${escapeHtml(fileUrl || previewUrl)}" data-preview-name="${escapeHtml(fileName)}" data-preview-kind="image">${media}<span>点击放大</span></div>`;
}

function renderTable(items, columns, tableClass = '', tableKey = '', includeColumnControls = false) {
  if (!items.length) {
    return '<p class="hint">暂无记录</p>';
  }
  const head = columns.map((column, index) => renderTableCell('th', column, index, tableKey)).join('');
  const body = items.map((item) => `<tr>${columns.map((column, index) => renderTableCell('td', item[column] ?? '', index, tableKey)).join('')}</tr>`).join('');
  const className = tableClass ? ` class="${tableClass}"` : '';
  const tableAttribute = tableKey ? ` data-table-key="${escapeHtml(tableKey)}"` : '';
  const controls = includeColumnControls ? renderColumnControls(tableKey, columns) : '';
  return `${controls}<div class="table-frame"><table${className}${tableAttribute}><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderTableCell(tagName, value, columnIndex, tableKey) {
  const hiddenClass = tableKey && !isColumnVisible(tableKey, columnIndex) ? ' class="is-hidden-column"' : '';
  const tableAttribute = tableKey ? ` data-table-key="${escapeHtml(tableKey)}"` : '';
  return `<${tagName}${hiddenClass}${tableAttribute} data-column-index="${columnIndex}">${escapeHtml(value)}</${tagName}>`;
}

function renderColumnControls(tableKey, columns) {
  const toggles = columns.map((column, index) => {
    const checked = isColumnVisible(tableKey, index) ? ' checked' : '';
    return `<label class="column-toggle"><input type="checkbox" data-column-toggle="${escapeHtml(tableKey)}" data-column-index="${index}"${checked}>${escapeHtml(column)}</label>`;
  }).join('');
  return `<div class="column-control-panel" aria-label="报销清单列显示设置"><span>显示列</span><div>${toggles}</div></div>`;
}

function isColumnVisible(tableKey, columnIndex) {
  if (!tableColumnVisibility[tableKey]) {
    tableColumnVisibility[tableKey] = {};
  }
  return tableColumnVisibility[tableKey][columnIndex] !== false;
}

function toggleColumnVisibility(tableKey, columnIndex, visible) {
  if (!tableColumnVisibility[tableKey]) {
    tableColumnVisibility[tableKey] = {};
  }
  tableColumnVisibility[tableKey][columnIndex] = visible;
  document.querySelectorAll(`[data-table-key="${cssEscape(tableKey)}"][data-column-index="${columnIndex}"]`).forEach((cell) => {
    cell.classList.toggle('is-hidden-column', !visible);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

async function choosePath(target, kind) {
  const response = await fetch(`/choose-path?kind=${encodeURIComponent(kind)}`);
  const data = await response.json();
  if (data.error) {
    alert(data.error);
    return;
  }
  if (data.path) {
    document.getElementById(target).value = data.path;
  }
}

window.choosePath = choosePath;
window.toggleColumnVisibility = toggleColumnVisibility;

previewPanel.addEventListener('change', function(event) {
  const toggle = event.target.closest('[data-column-toggle]');
  if (!toggle) {
    return;
  }
  toggleColumnVisibility(toggle.dataset.columnToggle, toggle.dataset.columnIndex, toggle.checked);
});

previewPanel.addEventListener('click', function(event) {
  const editToggle = event.target.closest('[data-review-edit-toggle]');
  if (editToggle) {
    const card = editToggle.closest('.review-card');
    if (card) {
      card.classList.add('is-editing');
    }
    return;
  }
  const editCancel = event.target.closest('[data-review-edit-cancel]');
  if (editCancel) {
    const card = editCancel.closest('.review-card');
    if (card) {
      card.classList.remove('is-editing');
    }
    return;
  }
  const previewButton = event.target.closest('[data-preview-url]');
  if (previewButton) {
    openPreviewLightbox(
      previewButton.dataset.previewUrl,
      previewButton.dataset.previewName,
      previewButton.dataset.fileUrl
    );
    return;
  }
  const overviewRow = event.target.closest('[data-review-target]');
  if (overviewRow) {
    scrollToReviewCard(overviewRow.dataset.reviewTarget);
  }
});

previewPanel.addEventListener('submit', async function(event) {
  const form = event.target.closest('[data-review-edit]');
  if (!form) {
    return;
  }
  event.preventDefault();
  await saveReviewEdits(form);
});

previewPanel.addEventListener('error', function(event) {
  if (event.target && event.target.matches('.review-preview-button img')) {
    handlePreviewImageError(event.target);
  }
}, true);

previewPanel.addEventListener('keydown', function(event) {
  const previewButton = event.target.closest('[data-preview-url]');
  if (previewButton && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    openPreviewLightbox(
      previewButton.dataset.previewUrl,
      previewButton.dataset.previewName,
      previewButton.dataset.fileUrl
    );
    return;
  }
  const overviewRow = event.target.closest('[data-review-target]');
  if (!overviewRow || (event.key !== 'Enter' && event.key !== ' ')) {
    return;
  }
  event.preventDefault();
  scrollToReviewCard(overviewRow.dataset.reviewTarget);
});

async function saveReviewEdits(form) {
  if (!currentTaskId) {
    return;
  }
  const sequence = form.dataset.reviewEdit;
  const button = form.querySelector('.review-save-button');
  const status = form.querySelector('.review-save-status');
  button.disabled = true;
  status.textContent = '保存中...';
  try {
    const baseUrl = currentPackageId
      ? `/tasks/${encodeURIComponent(currentTaskId)}/packages/${encodeURIComponent(currentPackageId)}`
      : `/tasks/${encodeURIComponent(currentTaskId)}`;
    const response = await fetch(`${baseUrl}/records/${encodeURIComponent(sequence)}`, {
      method: 'POST',
      body: new URLSearchParams(new FormData(form))
    });
    const data = await response.json();
    if (data.error) {
      throw new Error(data.error);
    }
    renderPreview(data.preview || null);
    const updatedForm = previewPanel.querySelector(`[data-review-edit="${cssEscape(sequence)}"]`);
    const updatedStatus = updatedForm ? updatedForm.querySelector('.review-save-status') : null;
    if (updatedStatus) {
      updatedStatus.textContent = '已保存';
    }
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
    button.disabled = false;
  }
}

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    closePreviewLightbox();
  }
});

function scrollToReviewCard(sequence) {
  const card = document.querySelector(`[data-review-sequence="${cssEscape(sequence)}"]`);
  if (!card) {
    return;
  }
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  card.classList.add('is-highlighted');
  window.setTimeout(() => card.classList.remove('is-highlighted'), 1200);
}

function handlePreviewImageError(image) {
  const previewButton = image.closest('[data-preview-url]');
  if (!previewButton || previewButton.dataset.previewFailed === '1') {
    return;
  }
  previewButton.dataset.previewFailed = '1';
  previewButton.classList.add('is-preview-failed');
  const fileName = previewButton.dataset.previewName || '原文件';
  previewButton.innerHTML = `<div class="preview-fallback preview-inline-fallback"><strong>${escapeHtml(fileName)}</strong><span>缩略图生成失败，可点击放大查看原文件</span><span class="preview-fallback-actions">打开原文件</span></div>`;
}

function openPreviewLightbox(url, name, fileUrl = url) {
  closePreviewLightbox();
  const safeUrl = escapeHtml(url);
  const safeFileUrl = escapeHtml(fileUrl || url);
  const safeName = escapeHtml(name);
  const content = `<img src="${safeUrl}" alt="${safeName}">`;
  const lightbox = document.createElement('div');
  lightbox.className = 'preview-lightbox';
  lightbox.innerHTML = `<div class="preview-lightbox-backdrop" data-lightbox-close></div>
    <div class="preview-lightbox-dialog" role="dialog" aria-modal="true" aria-label="${safeName}">
      <div class="preview-lightbox-head"><strong>${safeName}</strong><div class="preview-lightbox-actions"><a href="${safeFileUrl}" target="_blank" rel="noopener">打开原文件</a><button type="button" data-lightbox-close>关闭</button></div></div>
      <div class="preview-lightbox-body">${content}</div>
    </div>`;
  lightbox.addEventListener('click', function(event) {
    if (event.target.closest('[data-lightbox-close]')) {
      closePreviewLightbox();
    }
  });
  document.body.appendChild(lightbox);
}

function closePreviewLightbox() {
  const lightbox = document.querySelector('.preview-lightbox');
  if (lightbox) {
    lightbox.remove();
  }
}

function cssEscape(value) {
  if (window.CSS && window.CSS.escape) {
    return window.CSS.escape(value);
  }
  return String(value).replace(/"/g, '\\"');
}

const initialTaskId = document.body.dataset.initialTaskId || '';
if (initialTaskId) {
  currentTaskId = initialTaskId;
  button.disabled = true;
  button.textContent = '整理中...';
  setTaskState('running');
  statusBox.classList.add('active');
  statusBox.innerHTML = '<span class="spinner"></span>任务已提交，正在准备...';
  panel.classList.add('active');
  pollTask(initialTaskId);
} else {
  setTaskState('idle');
}
