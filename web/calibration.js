(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const missing = '未获得';
  const modelNames = ['zero_change', 'state_ridge', 'publication_ridge'];
  const labels = {
    created_at: '报告生成时间', sample_unit: '样本单位', target_unit: '目标单位', unit: '单位',
    start: '开始日期', end: '结束日期', start_date: '开始日期', end_date: '结束日期',
    date_range: '日期范围', train: '训练集', validation: '验证集', test: '留出集',
    split: '时间分割', cutoff: '分割日期', train_end: '训练截止', test_start: '留出开始',
    test_end: '留出截止', n: '样本数', n_train: '训练样本数', n_test: '留出样本数',
    source: '数据来源', sources: '数据来源', status: '状态', reason: '说明',
    mae: 'MAE', actual_coverage: '实际覆盖', coverage: '覆盖（报告原字段）',
    nominal_coverage: '名义覆盖', interval: '区间', lower: '下界', upper: '上界',
    support: '支持范围', settings: '设置', evaluation: '评估', summary: '摘要',
    count: '记录数', eligible_count: '符合条件数', observed_count: '观测数',
    train_until: '训练截止', calibration_until: '校准截止', cutoffs: '时间边界',
    calibration: '校准集', purged: '跨分割边界剔除', future_excluded: '未来窗口排除',
    n_rows: '样本行数', n_units: '基金数', n_target_days: '目标日数',
    n_publication_exposed: '发布暴露样本行数', n_support_post_rows: '有支持帖子的样本行数',
    origin_at: '起点可用时间', target_at: '目标可用时间', min: '最早', max: '最晚',
    alpha: '岭回归正则强度', requested_coverage: '请求覆盖率', day_timezone: '目标日分组时区',
    all: '全部留出样本', publication_exposed: '发布暴露留出子集', n_intervals: '有区间样本数',
    observed_coverage: '实际覆盖率', mean_width: '平均区间宽度', rmse: 'RMSE',
    mae_delta: '按样本行 MAE 差值', daily_mean_mae_delta: '按目标日等权 MAE 差值',
    bootstrap: '目标日区块自助区间', ci: '置信区间', estimand: '估计量',
    confidence_level: '置信水平', method: '方法', block_days: '区块天数', n_resamples: '重采样次数',
    n_valid_resamples: '有效重采样次数', seed: '随机种子', note: '说明',
    rows: '有效样本行数', nav_source_rows: '净值源记录数', source_funds: '来源基金数', funds: '有效样本基金数',
    dated_linked_posts: '有时间且已关联的帖子数', used_source_posts: '实际使用的不同帖子数',
    publication_supported_rows: '有帖子支持的样本行数', origin_dates: '不同起点时间数', feature_names: '特征名称',
    nav_invalid_identity: '净值身份无效记录', nav_invalid_date: '净值日期无效记录',
    nav_duplicate_rows: '净值重复记录', nav_conflicting_rows: '净值冲突记录', nav_invalid_value_rows: '净值无效记录',
    post_source_rows: '帖子源记录数', post_invalid_identity: '帖子身份无效记录', post_duplicate_rows: '帖子重复记录',
    post_conflicting_keys: '帖子冲突主键数', cn_posts: '中国市场帖子数', post_without_valid_time: '缺少有效时间的帖子',
    post_invalid_fund_list: '基金关联列表无效的帖子', post_without_fund_link: '未关联基金的帖子',
    post_fund_links_without_nav: '缺少净值的帖子基金关联', candidate_origin_rows: '候选起点行数',
    rows_excluded_invalid_nav: '因净值无效排除的样本', rows_excluded_gap: '因间隔过长排除的样本',
    rows_excluded_target_after_end: '因目标超出结束日期排除的样本', max_gap_days: '最大观测间隔天数',
    lookback_changes: '回看净值变化次数', publication_lookback_days: '发布特征回看天数', target: '目标定义',
    post_rows: '帖子记录数', unique_posts: '不同帖子数', duplicate_post_rows: '重复帖子记录数',
    conflicting_post_ids: '冲突帖子数', snapshot_rows: '快照记录数', snapshot_posts: '有快照帖子数',
    crawl_dates: '抓取日期', snapshot_rows_by_date: '各抓取日期记录数', unique_dated_snapshot_keys: '不同帖子日期键数',
    duplicate_snapshot_rows: '重复快照记录数', conflicting_snapshot_keys: '冲突快照键数',
    invalid_crawl_date_rows: '抓取日期无效记录数', posts_excluded_unknown_chronology: '因日期未知排除的帖子数',
    snapshot_posts_absent_from_posts: '源帖子缺失的快照帖子数', posts_without_snapshots: '无快照帖子数',
    snapshot_posts_with_at_most_one_usable_snapshot: '至多一个可用快照的帖子数',
    adjacent_pairs_excluded_conflict: '因冲突排除的相邻快照对', intervals: '相邻快照区间数',
    posts_with_intervals: '具有区间的帖子数', interval_publication_statuses: '区间发布时间状态',
    metric_interval_statuses: '各指标区间状态', metric_eligible_pairs: '各指标合格快照对数',
    likes: '点赞', collections: '收藏', comments: '评论', shares: '分享',
    counter_decrease: '计数下降', inexact_endpoint: '端点计数非精确', ok: '可用',
    missing: '缺失', approximate: '近似值', invalid: '无效', exact: '精确值', valid: '有效',
    future: '发布时间晚于区间起点', post_missing: '帖子缺失', post_conflict: '帖子冲突',
    crawl_date: '抓取日期', raw_rows: '原始记录数', unique_keys: '不同帖子日期键数',
    conflicting_keys: '冲突键数', usable_keys: '可用键数', metrics: '计数质量',
    horizon: '预测跨度', lookback_days: '回看天数', limitations: '局限',
  };
  // Only summary fields are rendered. Never expand row-level prediction artifacts.
  const omitted = /^(predictions?|row_ids|daily|daily_metrics|records|raw|files|artifacts|downloads?|.*_path|.*_url)$/i;
  const statuses = { ok: '可用', insufficient_support: '支持不足', insufficient_training: '训练数据不足', insufficient_calibration: '校准数据不足', unavailable: '未获得', fitted: '已拟合', fixed: '固定参照' };
  const percent = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : missing;
  let snapshots = [];
  let generation = 0;
  let controller;
  function node(tag, content) {
    const el = document.createElement(tag);
    if (content !== undefined) el.textContent = content;
    return el;
  }
  function scalar(value) {
    if (value === null || value === undefined || value === '') return missing;
    if (typeof value === 'number') return Number.isFinite(value) ? String(value) : missing;
    if (typeof value === 'boolean') return value ? '是' : '否';
    return String(value);
  }
  function fact(dl, key, value) {
    const row = node('div'); row.className = 'fact';
    row.append(node('dt', key), node('dd', scalar(value))); dl.append(row);
  }
  function summary(dl, data) {
    dl.replaceChildren();
    let count = 0;
    function walk(value, path, depth) {
      if (count >= 80) return;
      if (Array.isArray(value)) {
        if (value.every(item => item === null || typeof item !== 'object')) {
          fact(dl, path, value.length ? value.slice(0, 20).map(scalar).join('、') + (value.length > 20 ? `（展示前 20 / ${value.length} 项）` : '') : '无条目'); count++;
        } else if (depth < 4) value.slice(0, 12).forEach((item, i) => walk(item, `${path} / ${i + 1}`, depth + 1));
      } else if (value && typeof value === 'object' && depth < 4) {
        Object.entries(value).forEach(([key, item]) => {
          if (!omitted.test(key) && key !== 'models' && !(['rows', 'intervals'].includes(key) && Array.isArray(item))) walk(item, [path, labels[key] || key].filter(Boolean).join(' / '), depth + 1);
        });
      } else if (!value || typeof value !== 'object') { fact(dl, path || '摘要', value); count++; }
    }
    walk(data, '', 0);
    if (!count) fact(dl, '摘要', missing);
    if (count >= 80) fact(dl, '展示范围', '仅展示前 80 项摘要字段');
  }
  function status(message, state) { $('status').textContent = message; $('status').dataset.state = state; }
  function begin(message) {
    generation++;
    if (controller) controller.abort();
    controller = new AbortController();
    $('report-content').hidden = true;
    $('report-content').setAttribute('aria-busy', 'true');
    status(message, 'loading');
    return { id: generation, signal: controller.signal };
  }
  async function json(url, signal) {
    const response = await fetch(url, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`);
    return response.json();
  }
  function render(report, tag) {
    if (!report || report.kind !== 'offline_temporal_calibration') throw new Error('报告格式不受支持，需 offline_temporal_calibration 摘要');
    $('report-title').textContent = report.label || tag;
    const metadata = $('metadata'); metadata.replaceChildren();
    fact(metadata, '报告标识', tag); fact(metadata, '报告生成时间（原记录时区）', report.created_at);
    fact(metadata, '时效说明', '历史静态产物；生成时间不等于数据覆盖截止时间');
    const nav = report.nav || {};
    const evaluation = nav.evaluation || {};
    const models = evaluation.models || {};
    const zero = models.zero_change?.metrics?.all;
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const comparison = ['state_ridge', 'publication_ridge'].map(name => {
      const candidate = models[name]?.metrics?.all;
      if (!finite(candidate?.mae) || !finite(zero?.mae) || !candidate.n || candidate.n !== zero.n) return `${name} 与 zero_change 的同范围比较未获得`;
      return `${name} 的全部留出 MAE ${candidate.mae < zero.mae ? '低于' : candidate.mae === zero.mae ? '等于' : '高于'} zero_change${candidate.mae >= zero.mae ? '，未优于零变化参照' : '（仅本次留出描述）'}`;
    });
    fact(metadata, '本次留出比较', comparison.join('；'));
    fact(metadata, '证据单位', '基金观测行不是独立人数或实验运行数；这是单次探索性时间留出。');
    $('models').replaceChildren();
    modelNames.forEach(name => {
      const values = models[name] || {};
      const all = values.metrics?.all || {};
      const exposed = values.metrics?.publication_exposed || {};
      const card = node('section'); card.className = 'model'; card.append(node('h3', name));
      card.append(node('p', ({zero_change: '固定零变化参照', state_ridge: '历史净值状态特征', publication_ridge: '历史状态 + 过去 7 天发布计数'})[name]));
      const metric = node('p', scalar(all.mae)); metric.className = 'metric'; card.append(metric);
      card.append(node('p', '全部留出样本 MAE · 原始净值对数变化'));
      const dl = node('dl');
      fact(dl, '模型状态', statuses[values.status] || values.status);
      fact(dl, '全部留出样本数', all.n);
      fact(dl, '全部样本 RMSE', all.rmse);
      fact(dl, '实际覆盖率', percent(all.observed_coverage));
      fact(dl, '覆盖率分母（有区间样本数）', all.n_intervals);
      fact(dl, '平均区间宽度', all.mean_width);
      fact(dl, '请求覆盖率（无保证）', percent(evaluation.requested_coverage));
      fact(dl, '覆盖对照', finite(all.observed_coverage) && finite(evaluation.requested_coverage)
        ? all.observed_coverage < evaluation.requested_coverage ? '实际覆盖低于请求覆盖率' : '本次实际覆盖达到请求值；不代表未来覆盖保证'
        : missing);
      fact(dl, '发布暴露子集样本数', exposed.n);
      fact(dl, '发布暴露子集 MAE', exposed.mae);
      fact(dl, '发布暴露子集实际覆盖率', percent(exposed.observed_coverage));
      fact(dl, '子集覆盖率分母', exposed.n_intervals);
      fact(dl, '校准样本数', values.calibration?.n);
      fact(dl, '校准区间半径', values.calibration?.radius);
      fact(dl, '校准状态', statuses[values.calibration?.status] || values.calibration?.status);
      card.append(dl); $('models').append(card);
    });
    summary($('evaluation-summary'), {
      '评估状态': statuses[evaluation.status] || evaluation.status,
      '缺失分割': Array.isArray(evaluation.missing_splits) ? evaluation.missing_splits.length ? evaluation.missing_splits.map(key => labels[key] || key).join('、') : '无' : null,
      '样本单位': '一只基金从起点到下一次观测净值的一步变化；跨基金共享目标日',
      cutoffs: evaluation.cutoffs, day_timezone: evaluation.day_timezone, alpha: evaluation.alpha,
      '模型选择': evaluation.selected_model === null ? '未选择；留出指标只作描述性比较' : evaluation.selected_model,
      '分割': evaluation.splits,
    });
    // Render only the paired aggregate, never the per-day raw comparison array.
    summary($('paired'), evaluation.paired_comparisons?.publication_ridge_minus_state_ridge?.strata);
    $('evaluation-notes').replaceChildren();
    (evaluation.notes || []).forEach(value => $('evaluation-notes').append(node('li', scalar(value))));
    summary($('support'), nav.support); summary($('settings'), nav.settings);
    const engagementSupport = report.engagement?.support;
    // Per-date detail lives in the selector, so long timelines cannot crowd out eligibility counts.
    summary($('engagement'), engagementSupport ? Object.fromEntries(Object.entries(engagementSupport).filter(([key]) => !['crawl_dates', 'snapshot_rows_by_date'].includes(key))) : null);
    if (Array.isArray(engagementSupport?.crawl_dates)) {
      fact($('engagement'), '抓取日期数', engagementSupport.crawl_dates.length);
      fact($('engagement'), '最早抓取日期', engagementSupport.crawl_dates[0]);
      fact($('engagement'), '最晚抓取日期', engagementSupport.crawl_dates[engagementSupport.crawl_dates.length - 1]);
    }
    snapshots = Array.isArray(report.engagement?.snapshot_summaries) ? report.engagement.snapshot_summaries : [];
    const snapshotSelect = $('snapshot-select'); snapshotSelect.replaceChildren(); snapshotSelect.disabled = !snapshots.length;
    snapshots.forEach((item, index) => { const option = node('option', scalar(item.crawl_date)); option.value = String(index); snapshotSelect.append(option); });
    if (!snapshots.length) { const option = node('option', '未获得快照摘要'); option.value = ''; snapshotSelect.append(option); }
    snapshotSelect.value = snapshots.length ? '0' : '';
    summary($('snapshot-summary'), snapshots[0]);
    $('limitations').replaceChildren();
    const combined = [...(Array.isArray(report.limitations) ? report.limitations : []), ...(Array.isArray(report.engagement?.limitations) ? report.engagement.limitations : [])];
    const limitations = combined.length ? [...new Set(combined)] : ['报告未提供局限说明；不能据此视为不存在局限。'];
    limitations.forEach(value => $('limitations').append(node('li', scalar(value))));
    $('report-content').hidden = false;
  }
  async function loadReport(tag) {
    const request = begin('正在读取报告摘要…');
    try {
      const report = await json(`/api/community/calibration/report?report=${encodeURIComponent(tag)}`, request.signal);
      if (request.id !== generation) return;
      render(report, tag); status('已加载离线报告。仅展示聚合摘要。', 'ready');
    } catch (error) {
      if (request.id !== generation) return;
      status(`${error.message || '报告读取失败'}。可重新加载后重试。`, 'error');
    } finally { if (request.id === generation) $('report-content').setAttribute('aria-busy', 'false'); }
  }
  async function loadReports() {
    const previous = $('report-select').value;
    const request = begin('正在读取报告列表…');
    const select = $('report-select'); select.disabled = true;
    select.replaceChildren(node('option', '正在读取报告列表…'));
    try {
      const payload = await json('/api/community/calibration/reports', request.signal);
      if (request.id !== generation) return;
      if (!payload || !Array.isArray(payload.reports)) throw new Error('报告列表格式不正确');
      const reports = payload.reports.filter(item => item && typeof item.tag === 'string' && item.tag.trim());
      select.replaceChildren();
      if (!reports.length) {
        const option = node('option', '暂无离线报告'); option.value = ''; select.append(option);
        status('暂无可用的离线校准报告。生成聚合产物后可重新加载。', 'empty'); return;
      }
      reports.forEach(item => { const option = node('option', item.label || item.tag); option.value = item.tag; select.append(option); });
      select.value = reports.some(item => item.tag === previous) ? previous : reports[0].tag;
      select.disabled = false;
      await loadReport(select.value);
    } catch (error) {
      if (request.id !== generation) return;
      const option = node('option', '报告列表不可用'); option.value = ''; select.replaceChildren(option);
      status(`${error.message || '报告列表读取失败'}。请重新加载。`, 'error');
    } finally { if (request.id === generation) $('report-content').setAttribute('aria-busy', 'false'); }
  }
  $('report-select').addEventListener('change', () => { if ($('report-select').value) loadReport($('report-select').value); });
  $('reload').addEventListener('click', loadReports);
  $('snapshot-select').addEventListener('change', () => summary($('snapshot-summary'), snapshots[Number($('snapshot-select').value)]));
  loadReports();
}());
