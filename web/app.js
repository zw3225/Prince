const state = {
  search: null,
  summary: null,
  content: [],
  creators: [],
  products: [],
  opportunities: [],
  sourceHealth: [],
  activeTab: "content",
  selectedId: null,
};

const collections = {
  content: { label: "爆款社媒内容", type: "content" },
  creators: { label: "垂类达人", type: "creator" },
  opportunities: { label: "机会洞察", type: "opportunity" },
};

const visibleCollectionKeys = ["content", "creators", "opportunities"];

const countFormat = new Intl.NumberFormat("zh-CN");

const sourceTypeLabels = {
  official_api: "官方 API",
  authorized_api: "授权 API",
  third_party: "第三方数据",
  compliance_scrape: "合规采集",
  sample: "演示数据",
};

const cadenceLabels = {
  daily: "每日",
};

const statusLabels = {
  live_ready: "已接入",
  public_json_ready: "公开 JSON",
  public_page_ready: "公开页面",
  search_discovery_ready: "搜索发现",
  missing_credentials: "需授权",
  not_connected: "未接入",
  demo_ready: "演示兜底",
  demo_only: "演示模式",
  fetch_error: "请求失败",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`请求失败：${response.status}`);
  }
  return response.json();
}

async function bootstrap() {
  setLoading(true);
  try {
    const payload = await api("/api/bootstrap");
    applyPayload(payload);
    render();
  } finally {
    setLoading(false);
  }
}

function applyPayload(payload) {
  state.search = payload.search;
  state.summary = payload.summary;
  state.content = directItems(payload.content || []);
  state.creators = directItems(payload.creators || []);
  state.products = directItems(payload.products || []);
  state.opportunities = directItems(payload.opportunities || []);
  state.sourceHealth = payload.source_health || [];
  syncSearchControls();
  ensureVisibleTab();
}

function directItems(items) {
  return items.filter((item) => item.url && item.source_url_type && item.source_url_type !== "sample");
}

function syncSearchControls() {
  const queryInput = document.querySelector("#queryInput");
  const windowSelect = document.querySelector("#windowSelect");
  if (queryInput && state.search?.query) queryInput.value = state.search.query;
  if (windowSelect && state.search?.window_days) windowSelect.value = String(state.search.window_days);
  const selectedMarkets = new Set(state.search?.markets || []);
  if (selectedMarkets.size) {
    document.querySelectorAll(".market-toggles input").forEach((input) => {
      input.checked = selectedMarkets.has(input.value);
    });
  }
}

async function createSearch(event) {
  event.preventDefault();
  const query = document.querySelector("#queryInput").value;
  const windowDays = Number(document.querySelector("#windowSelect").value);
  const markets = [...document.querySelectorAll(".market-toggles input:checked")].map((item) => item.value);
  setLoading(true);
  try {
    state.selectedId = null;
    const created = await api("/api/trend-searches", {
      method: "POST",
      body: JSON.stringify({ query, window_days: windowDays, markets }),
    });
    const searchId = created.search.id;
    const [content, creators, opportunities, sourceHealth] = await Promise.all([
      api(`/api/rankings/content?search_id=${searchId}`),
      api(`/api/rankings/creators?search_id=${searchId}`),
      api(`/api/rankings/opportunities?search_id=${searchId}`),
      api("/api/sources/health"),
    ]);
    applyPayload({
      search: created.search,
      summary: created.summary,
      content: content.items,
      creators: creators.items,
      opportunities: opportunities.items,
      source_health: sourceHealth.sources,
    });
    render();
  } finally {
    setLoading(false);
  }
}

function render() {
  renderMarkets();
  renderSources();
  renderMetrics();
  renderNarrative();
  renderTabs();
  renderTable();
  renderSelectedDetail();
}

function renderMarkets() {
  const markets = state.search?.markets || ["US", "UK", "CA", "AU"];
  document.querySelector("#marketList").innerHTML = markets
    .map((market) => `<div class="market-chip market-${marketClass(market)}">${marketBadgeMarkup(market)}<span>已选</span></div>`)
    .join("");
}

function renderSources() {
  document.querySelector("#sourceList").innerHTML = state.sourceHealth
    .map(
      (source) => `
        <div class="source-item">
          <div class="source-row">
            <span class="source-name">${source.platform}</span>
            <span class="source-status ${sourceStatusClass(source.status)}">${statusLabel(source.status)}</span>
          </div>
          <div class="source-meta">${sourceTypeLabel(source.source_type)} · ${cadenceLabel(source.cadence)}</div>
          <div class="source-note">${source.coverage_note || ""}</div>
        </div>
      `,
    )
    .join("");
}

function renderMetrics() {
  const totals = {
    content: state.content.length,
    creators: state.creators.length,
    opportunities: state.opportunities.length,
  };
  const metrics = [
    ["内容入口", totals.content, metricStatus("content", totals.content)],
    ["达人入口", totals.creators, metricStatus("creators", totals.creators)],
    ["机会入口", totals.opportunities, metricStatus("opportunities", totals.opportunities)],
  ];
  document.querySelector("#metricStrip").innerHTML = metrics
    .map(
      ([label, value, sub]) => `
        <div class="metric ${value ? "" : "metric-empty"}">
          <strong>${value}</strong>
          <span>${label} · ${sub}</span>
        </div>
      `,
    )
    .join("");
}

function metricStatus(key, value) {
  if (value) {
    return {
      content: "可直达帖子 / 视频",
      creators: "可直达主页",
      opportunities: "可验证来源",
    }[key];
  }
  return {
    content: "本轮未抓到可验证内容入口",
    creators: "本轮未抓到可验证达人主页",
    opportunities: "暂无可验证机会入口",
  }[key];
}

function renderNarrative() {
  const rows = insightRows();
  document.querySelector("#narrativeList").innerHTML = rows.map((item) => insightRowMarkup(item)).join("");
  const tags = visibleTopTags(state.summary?.top_tags || []);
  document.querySelector("#tagCloud").innerHTML = tags.length
    ? tags.map((item) => `<span class="tag"><span>${item.tag}</span><strong>${formatCount(item.count)}</strong></span>`).join("")
    : '<span class="tag tag-empty">暂无关联线索</span>';
}

function insightRows() {
  const rows = [];
  const firstContent = state.content[0];
  const firstCreator = state.creators[0];
  const firstOpportunity = state.opportunities[0];

  rows.push(
    firstContent
      ? {
          label: "内容需求",
          value: `${formatCount(state.content.length)} 条可打开内容`,
          detail: `先看 ${firstContent.platform}：${firstContent.title}`,
          note: hasUsableViews(firstContent) ? `${formatCount(firstContent.metrics.views)} 次播放` : "打开后核对播放和互动",
        }
      : {
          label: "内容需求",
          value: "暂无可打开内容",
          detail: "本轮没有拿到可直达原帖或视频详情的链接。",
          note: "不展示搜索页",
        },
  );

  rows.push(
    firstCreator
      ? {
          label: "达人验证",
          value: `${formatCount(state.creators.length)} 个可打开主页`,
          detail: `先核对 ${firstCreator.platform}：${firstCreator.author || firstCreator.title}`,
          note: "确认主页状态和调性",
        }
      : {
          label: "达人验证",
          value: "暂无可打开主页",
          detail: "本轮没有拿到可直达达人主页的链接。",
          note: "不用猜测主页",
        },
  );

  rows.push(
    firstOpportunity
      ? {
          label: "商品机会",
          value: `${formatCount(state.opportunities.length)} 个可打开商详`,
          detail: `先核对 ${firstOpportunity.platform}：${cleanOpportunityTitle(firstOpportunity.title)}`,
          note: firstOpportunity.price ? `已抓到价格 ${formatPrice(firstOpportunity.price)}` : "价格需点开确认",
        }
      : {
          label: "商品机会",
          value: "暂无可打开商详",
          detail: "本轮没有拿到可直达商品详情或原始来源的链接。",
          note: "不展示站内搜索页",
        },
  );

  if (!state.content.length) {
    rows.push({
      label: "缺口提示",
      value: "内容入口未抓到",
      detail: "内容入口只展示可直达原帖或视频详情的链接；本轮公开来源没有返回可验证内容入口。",
      note: "可换关键词或扩大时间窗",
    });
  }
  if (!state.creators.length) {
    rows.push({
      label: "缺口提示",
      value: "达人主页未抓到",
      detail: "达人入口只展示可直达主页的链接；本轮没有用搜索页或猜测主页冒充达人结果。",
      note: "可换关键词或扩大时间窗",
    });
  }
  return rows;
}

function insightRowMarkup(item) {
  return `
    <div class="insight-row">
      <span class="insight-label">${item.label}</span>
      <div class="insight-copy">
        <strong>${item.value}</strong>
        <span>${item.detail}</span>
      </div>
      <span class="insight-note">${item.note}</span>
    </div>
  `;
}

function visibleTopTags(tags) {
  return tags
    .filter((item) => item.tag && item.tag.trim() && item.tag.trim() !== "#")
    .slice(0, 8);
}

function cleanOpportunityTitle(title) {
  return String(title || "").replace(/^机会：/, "");
}

function renderTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    const tabKey = tab.dataset.tab;
    if (!visibleCollectionKeys.includes(tabKey)) return;
    const count = (state[tabKey] || []).length;
    tab.textContent = `${collections[tabKey].label}（${count}）`;
    tab.classList.toggle("active", tabKey === state.activeTab);
  });
}

function renderTable() {
  const items = state[state.activeTab] || [];
  const metricColumn = primaryMetricColumnLabel();
  document.querySelector("#rankingHead").innerHTML = `
    <tr>
      <th>排序</th>
      <th>对象 / 入口</th>
      <th>平台 / 市场</th>
      <th>${metricColumn}</th>
    </tr>
  `;
  if (!items.length) {
    document.querySelector("#rankingBody").innerHTML = `
      <tr>
        <td colspan="4" class="empty-row">${emptyRankingText()}</td>
      </tr>
    `;
    return;
  }
  document.querySelector("#rankingBody").innerHTML = items
    .map((item, index) => {
      const metric = primaryMetric(item);
      return `
        <tr data-id="${item.id}" class="${state.selectedId === item.id ? "selected" : ""}" tabindex="0">
          <td><span class="rank-index">${index + 1}</span></td>
          <td>
            ${rowTitleMarkup(item)}
            <div class="rank-sub">${rowSubtext(item)}</div>
            ${rowSourceLinkMarkup(item)}
          </td>
          <td>
            <div class="platform-stack">
              <div class="platform">${item.platform}</div>
              ${marketBadgeMarkup(item.market)}
            </div>
          </td>
          <td>
            <div class="signal-stack">
              <strong>${metric.value}</strong>
              ${metric.note ? `<span>${metric.note}</span>` : ""}
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  document.querySelectorAll("#rankingBody tr").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      selectEntity(row.dataset.id);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectEntity(row.dataset.id);
      }
    });
  });
}

function primaryMetricColumnLabel() {
  if (state.activeTab === "content") return "播放量";
  if (state.activeTab === "creators") return "主页状态";
  if (state.activeTab === "opportunities") return "验证信号";
  return "价格";
}

function primaryMetric(item) {
  if (item.entity_type === "content") return contentMetric(item);
  if (item.entity_type === "creator") return creatorMetric(item);
  if (item.entity_type === "opportunity") return opportunityMetric(item);
  return productMetric(item);
}

function productMetric(item) {
  const price = item.price ? formatPrice(item.price) : "价格未抓到";
  const note = item.price ? priceSourceNote(item) : "公开页面没有返回可解析价格";
  return { label: "价格", value: price, note };
}

function opportunityMetric(item) {
  const metrics = item.metrics || {};
  const relatedCount = Number(metrics.related_content || 0) + Number(metrics.related_creators || 0);
  if (item.price) {
    return { label: "验证信号", value: formatPrice(item.price), note: "可打开商详核对价格" };
  }
  if (relatedCount) {
    return { label: "验证信号", value: `${formatCount(relatedCount)} 个相关入口`, note: "内容 / 达人 / 商品证据链" };
  }
  if (item.url) {
    return { label: "验证信号", value: "商详可打开", note: "价格需点开确认" };
  }
  return { label: "验证信号", value: "待确认", note: "本轮未拿到可验证入口" };
}

function contentMetric(item) {
  const metrics = item.metrics || {};
  if (hasUsableViews(item)) {
    return { label: "播放量", value: `${formatCount(metrics.views)} 次播放`, note: "" };
  }
  return { label: "播放量", value: "播放量未抓到", note: "该平台未公开播放量或本次未解析到" };
}

function creatorMetric(item) {
  if (item.url) {
    return { label: "主页状态", value: "主页可打开", note: "" };
  }
  return { label: "主页状态", value: "待点开确认", note: "本轮未拿到可直达达人主页" };
}

function hasUsableViews(item) {
  const views = Number(item.metrics?.views || 0);
  if (!views) return false;
  return item.platform === "YouTube";
}

function priceSourceNote(item) {
  const discount = item.discount ? `，${Math.round(item.discount * 100)}% 折扣` : "";
  return `从公开商品结果解析${discount}`;
}

function formatPrice(value) {
  return `$${Number(value).toFixed(2)}`;
}

async function selectEntity(id) {
  state.selectedId = id;
  renderTable();
  const item = currentItems().find((entry) => entry.id === id);
  if (!item) return;
  const type = collections[state.activeTab].type;
  const payload = await api(`/api/entities/${type}/${id}?search_id=${state.search.id}`);
  renderDetail(payload.entity, payload.related || []);
}

function renderDetail(entity, related) {
  const actions = verificationActions(entity);
  const evidence = visibleEvidenceEntries(entity);
  const missing = missingEvidenceEntries(entity);
  document.querySelector("#detailPanel").innerHTML = `
      <div class="detail-card">
        <div class="detail-top">
        <div class="detail-meta">
          ${marketBadgeMarkup(entity.market)}
          <span class="source-pill">${sourceTypeLabel(entity.source_type)}</span>
        </div>
        <span class="detail-platform">${entity.platform}</span>
      </div>
      <div class="detail-title">
        <h2>${entity.title}</h2>
        <p class="muted">${detailMetaText(entity)}</p>
      </div>
      ${sourceLinkMarkup(entity)}
      <section class="detail-section">
        <h3>这条结果能做什么</h3>
        <div class="action-list">${actions.map((action) => `<span>${action}</span>`).join("")}</div>
      </section>
      <section class="detail-section">
        <h3>已抓到的数据</h3>
        ${
          evidence.length
            ? `<div class="evidence-list">${evidence.map((item) => evidenceRowMarkup(item)).join("")}</div>`
            : '<p class="muted">只拿到了可打开入口，暂无可展示的真实指标。</p>'
        }
      </section>
      <section class="detail-section">
        <h3>待人工确认</h3>
        <ul class="check-list">${missing.map((item) => `<li>${item}</li>`).join("")}</ul>
      </section>
      <section class="detail-section">
        <h3>相关入口</h3>
        ${
          related.length
            ? related.slice(0, 5).map((item) => relatedItemMarkup(item)).join("")
            : '<p class="muted">暂无相关入口</p>'
        }
      </section>
    </div>
  `;
}

function evidenceRowMarkup(item) {
  const note = item.note ? `<small>${item.note}</small>` : "";
  return `
    <div class="evidence-row">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
      ${note}
    </div>
  `;
}

function relatedItemMarkup(item) {
  const title = item.url
    ? `<a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a>`
    : `<span>${item.title}</span>`;
  return `<p class="related-entry">${title}<small>${item.platform} · ${sourceTargetLabel(item)}</small></p>`;
}

function visibleEvidenceEntries(entity) {
  if (entity.entity_type === "content") return contentEvidenceEntries(entity);
  if (entity.entity_type === "creator") return creatorEvidenceEntries(entity);
  return productEvidenceEntries(entity);
}

function contentEvidenceEntries(entity) {
  const metrics = entity.metrics || {};
  const entries = [];
  if (hasUsableViews(entity)) entries.push({ label: "播放量", value: `${formatCount(metrics.views)} 次播放`, note: "平台公开内容页返回" });
  if (positiveMetric(metrics.comments)) entries.push({ label: "评论数", value: `${formatCount(metrics.comments)} 条` });
  if (positiveMetric(metrics.likes)) entries.push({ label: "点赞数", value: `${formatCount(metrics.likes)} 次` });
  if (positiveMetric(metrics.upvotes)) entries.push({ label: "赞同数", value: `${formatCount(metrics.upvotes)} 次` });
  return entries;
}

function creatorEvidenceEntries(entity) {
  const metrics = entity.metrics || {};
  const entries = [];
  if (positiveMetric(metrics.followers)) entries.push({ label: "粉丝量", value: `${formatCount(metrics.followers)} 粉丝`, note: "平台公开主页返回" });
  if (entity.url) entries.push({ label: "主页入口", value: sourceUrlPreview(entity.url), note: sourceTargetLabel(entity) });
  if (positiveMetric(metrics.total_views)) entries.push({ label: "频道总播放", value: `${formatCount(metrics.total_views)} 次播放` });
  return entries;
}

function productEvidenceEntries(entity) {
  const metrics = entity.metrics || {};
  const entries = [];
  if (entity.price) entries.push({ label: "价格", value: formatPrice(entity.price), note: "公开商品页解析" });
  if (entity.discount) entries.push({ label: "折扣", value: `${Math.round(entity.discount * 100)}%` });
  if (hasRealReviews(entity)) entries.push({ label: "评价数", value: `${formatCount(metrics.reviews)} 条` });
  if (hasRealRating(entity)) entries.push({ label: "商品评分", value: String(metrics.rating) });
  if (entity.entity_type === "opportunity") {
    if (positiveMetric(metrics.related_content)) entries.push({ label: "相关内容入口", value: `${formatCount(metrics.related_content)} 条` });
    if (positiveMetric(metrics.related_creators)) entries.push({ label: "相关达人主页", value: `${formatCount(metrics.related_creators)} 个` });
    if (positiveMetric(metrics.cross_platform_count)) entries.push({ label: "覆盖平台", value: `${formatCount(metrics.cross_platform_count)} 个` });
  }
  return entries;
}

function missingEvidenceEntries(entity) {
  const metrics = entity.metrics || {};
  const missing = [];
  if (!entity.url) {
    missing.push(sourceLinkUnavailableLabel(entity));
  }
  if (entity.entity_type === "content") {
    if (!hasUsableViews(entity)) missing.push("播放量未抓到");
    if (!positiveMetric(metrics.comments)) missing.push("评论数未抓到");
    if (!positiveMetric(metrics.likes) && !positiveMetric(metrics.upvotes)) missing.push("点赞 / 赞同数未抓到");
  } else if (entity.entity_type === "creator") {
    if (!positiveMetric(metrics.followers)) missing.push("粉丝量未抓到");
    if (!positiveMetric(metrics.total_views)) missing.push("频道总播放未抓到");
  } else {
    if (!entity.price) missing.push("价格未抓到");
    if (!hasRealReviews(entity)) missing.push("评价数未抓到");
    if (!hasRealRating(entity)) missing.push("真实商品评分未抓到");
  }
  if (needsManualSourceConfirmation(entity)) {
    missing.push("平台只返回了公开入口，详情需点开确认");
  }
  return missing.length ? missing : ["关键字段已抓到，建议点开原始页面复核页面状态。"];
}

function verificationActions(entity) {
  const actions = [];
  if (entity.source_url_type === "official_content" || entity.entity_type === "content") {
    actions.push("可直接打开原帖");
  }
  if (entity.source_url_type === "official_profile" || entity.entity_type === "creator") {
    actions.push("可核对达人主页");
  }
  if (entity.source_url_type === "official_product" || entity.entity_type === "product" || entity.entity_type === "opportunity") {
    actions.push("可查看商品详情");
  }
  if (entity.price) actions.push("可查看商品价格");
  if (positiveMetric(entity.metrics?.followers)) actions.push("可核对粉丝量");
  if (hasUsableViews(entity)) actions.push("可核对播放量");
  if (entity.entity_type === "content") actions.push("可作为内容参考");
  if (entity.entity_type === "creator") actions.push("可作为达人初筛");
  if (entity.entity_type === "product" || entity.entity_type === "opportunity") actions.push("可作为竞品 / 选品参考");
  return uniqueValues(actions).slice(0, 5);
}

function rowSubtext(item) {
  const author = item.author || item.platform;
  return `${author} · ${sourceTargetLabel(item)} · ${rowCapabilityText(item)}`;
}

function rowCapabilityText(item) {
  if (item.entity_type === "content") return hasUsableViews(item) ? "已抓到播放量" : "待点开核对播放量";
  if (item.entity_type === "creator") return item.url ? "主页可打开" : "待点开确认主页";
  if (item.entity_type === "opportunity") return item.url ? "商详可打开" : "待点开确认机会";
  return item.price ? "已抓到价格" : "待点开核对价格";
}

function positiveMetric(value) {
  return Number(value || 0) > 0;
}

function hasRealReviews(entity) {
  const reviews = Number(entity.metrics?.reviews || 0);
  return reviews > 0;
}

function hasRealRating(entity) {
  const rating = Number(entity.metrics?.rating || 0);
  if (!rating) return false;
  const fakeDefaults = new Set([4, 4.1, 4.2, 4.3]);
  return !fakeDefaults.has(Number(rating.toFixed(1)));
}

function needsManualSourceConfirmation(entity) {
  return ["compliance_scrape", "third_party"].includes(entity.source_type);
}

function formatCount(value) {
  return countFormat.format(Math.round(Number(value || 0)));
}

function uniqueValues(values) {
  return [...new Set(values.filter(Boolean))];
}

function currentItems() {
  return state[state.activeTab] || [];
}

function ensureVisibleTab() {
  if (!visibleCollectionKeys.includes(state.activeTab)) {
    state.activeTab = "opportunities";
  }
  if ((state[state.activeTab] || []).length) {
    ensureSelectedItem();
    return;
  }
  const firstNonEmpty = visibleCollectionKeys.find((key) => (state[key] || []).length);
  if (firstNonEmpty) {
    state.activeTab = firstNonEmpty;
  }
  ensureSelectedItem();
}

function ensureSelectedItem() {
  const items = currentItems();
  if (!items.length) {
    state.selectedId = null;
    return;
  }
  if (!items.some((item) => item.id === state.selectedId)) {
    state.selectedId = items[0].id;
  }
}

function renderSelectedDetail() {
  const item = currentItems().find((entry) => entry.id === state.selectedId);
  if (item) {
    renderDetail(item, []);
    return;
  }
  renderEmptyDetail();
}

function renderEmptyDetail() {
  document.querySelector("#detailPanel").innerHTML = `
    <div class="empty-detail">
      <h2>暂无可打开结果</h2>
      <p>换一个关键词或扩大时间窗后，这里会直接显示达人主页、内容详情或商品详情。</p>
    </div>
  `;
}

function sourceTypeLabel(value) {
  return sourceTypeLabels[value] || String(value).replaceAll("_", " ");
}

function cadenceLabel(value) {
  return cadenceLabels[value] || value;
}

function statusLabel(value) {
  return statusLabels[value] || value || "未知";
}

function sourceStatusClass(status) {
  if (["live_ready", "public_json_ready", "public_page_ready", "search_discovery_ready"].includes(status)) return "status-ready";
  if (status === "fetch_error") return "status-error";
  if (["missing_credentials", "not_connected"].includes(status)) return "status-pending";
  return "status-neutral";
}

function marketBadgeMarkup(market) {
  const label = marketLabel(market);
  return `<span class="market-badge market-${marketClass(label)}"><span class="market-dot" aria-hidden="true"></span>${label}</span>`;
}

function marketLabel(value) {
  const normalized = String(value || "GLOBAL").trim().toUpperCase();
  if (normalized === "GB") return "UK";
  return normalized || "GLOBAL";
}

function marketClass(value) {
  const normalized = marketLabel(value);
  if (["US", "UK", "CA", "AU", "GLOBAL"].includes(normalized)) return normalized.toLowerCase();
  return "global";
}

function detailMetaText(entity) {
  return uniqueValues([entity.author, entity.platform]).join(" · ");
}

function emptyRankingText() {
  if (state.activeTab === "content") {
    return socialEmptyText("内容入口", "帖子或视频详情");
  }
  if (state.activeTab === "creators") {
    return socialEmptyText("达人入口", "达人主页");
  }
  if (state.activeTab === "opportunities") {
    return commerceEmptyText("机会入口", "商品详情或原始来源");
  }
  if (state.sourceHealth.some((source) => source.status === "fetch_error")) {
    if (hasLocalNetworkFailure()) {
      return "暂无可用真实链接；本地看板服务刚才没有联网，请重新启动看板服务后再试。";
    }
    return "暂无可用真实链接；部分公开页面被平台拦截或超时，已隐藏不可直达结果。请换关键词、扩大市场/时间窗或稍后重试。";
  }
  if (state.sourceHealth.some((source) => source.status === "missing_credentials")) {
    return "暂无可用真实链接；当前来源需要授权或公开页面没有返回可解析结果。";
  }
  return "暂无可用真实链接；请换关键词、扩大时间窗或稍后再试。";
}

function socialEmptyText(label, target) {
  const failed = failedSources(["YouTube", "Reddit", "TikTok", "Instagram", "Pinterest"]);
  const suffix = failed.length ? `本轮 ${failed.join("、")} 公开抓取失败或超时。` : "公开平台本轮没有返回可验证结果。";
  return `${label}暂无可打开结果；这里只展示可直达${target}的真实链接，不展示站内搜索页或猜测链接。${suffix}`;
}

function commerceEmptyText(label, target) {
  const failed = failedSources(["Amazon", "eBay", "Etsy", "Walmart", "Target", "iHerb"]);
  const suffix = failed.length ? `本轮 ${failed.join("、")} 公开抓取失败或超时。` : "公开电商页本轮没有返回可验证结果。";
  return `${label}暂无可打开结果；这里只展示可直达${target}的真实链接，不展示站内搜索页。${suffix}`;
}

function failedSources(platforms) {
  const wanted = new Set(platforms);
  return state.sourceHealth
    .filter((source) => wanted.has(source.platform) && source.status === "fetch_error")
    .map((source) => source.platform);
}

function hasLocalNetworkFailure() {
  const notes = state.sourceHealth.map((source) => `${source.coverage_note || ""} ${source.status || ""}`).join(" ").toLowerCase();
  return [
    "winerror 10061",
    "connection refused",
    "actively refused",
    "failed to establish a new connection",
    "network is unreachable",
    "无法连接",
    "连接失败",
  ].some((pattern) => notes.includes(pattern));
}

function sourceLinkLabel(entity) {
  if (entity.source_url_type === "official_profile" || entity.entity_type === "creator") return "打开达人主页";
  if (entity.source_url_type === "official_product" || entity.entity_type === "product" || entity.entity_type === "opportunity") return "打开商品详情";
  if (entity.source_url_type === "official_content" || entity.entity_type === "content") return "打开内容详情";
  return "打开来源";
}

function sourceLinkMarkup(entity) {
  if (entity.url) {
    return `
      <a class="external-link" href="${entity.url}" target="_blank" rel="noreferrer">
        <span>${sourceLinkLabel(entity)}</span>
        <small>${sourceUrlPreview(entity.url)}</small>
      </a>
    `;
  }
  return `<span class="source-link-disabled">${sourceLinkUnavailableLabel(entity)}</span>`;
}

function rowTitleMarkup(item) {
  if (!item.url) return `<div class="rank-title">${item.title}</div>`;
  return `<a class="rank-title rank-title-link" href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a>`;
}

function rowSourceLinkMarkup(item) {
  if (!item.url) {
    return `<span class="row-link-disabled">${sourceLinkUnavailableLabel(item)}</span>`;
  }
  return `
    <a class="row-link" href="${item.url}" target="_blank" rel="noreferrer">
      <span>${sourceLinkLabel(item)}</span>
      <small>${sourceUrlPreview(item.url)}</small>
    </a>
  `;
}

function sourceTargetLabel(entity) {
  if (entity.source_url_type === "official_profile" || entity.entity_type === "creator") return "达人主页";
  if (entity.source_url_type === "official_product" || entity.entity_type === "product" || entity.entity_type === "opportunity") return "商品详情";
  if (entity.source_url_type === "official_content" || entity.entity_type === "content") return "内容详情";
  return "原始来源";
}

function sourceUrlPreview(value) {
  try {
    const url = new URL(value);
    const path = url.pathname === "/" ? "" : url.pathname;
    return `${url.hostname}${path}`.replace(/\/$/, "");
  } catch {
    return value || "";
  }
}

function sourceLinkUnavailableLabel(entity) {
  if (entity.entity_type === "creator") return "暂无达人主页链接";
  if (entity.entity_type === "product" || entity.entity_type === "opportunity") return "暂无商品详情链接";
  if (entity.entity_type === "content") return "暂无内容详情链接";
  return "暂无原始链接";
}

function setLoading(isLoading) {
  const button = document.querySelector("#searchButton");
  if (!button) return;
  button.disabled = isLoading;
  button.textContent = isLoading ? "生成中" : "生成雷达";
}

document.querySelector("#searchForm").addEventListener("submit", createSearch);
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.activeTab = tab.dataset.tab;
    state.selectedId = null;
    ensureSelectedItem();
    renderTabs();
    renderTable();
    renderSelectedDetail();
  });
});

bootstrap().catch((error) => {
  document.querySelector(".workspace").innerHTML = `<p>加载失败：${error.message}</p>`;
});
