document.addEventListener("DOMContentLoaded", () => {
  const showToast = (message) => {
    let toast = document.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toast.hideTimer);
    toast.hideTimer = window.setTimeout(() => toast.classList.remove("show"), 1800);
  };

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);

  const configDataElement = document.getElementById("config-page-data");
  let configPageData = {};
  try {
    configPageData = configDataElement ? JSON.parse(configDataElement.textContent || "{}") : {};
  } catch {
    configPageData = {};
  }

  const defaultConfigState = () => ({
    projectMappingOverrides: {},
    customProjectMappings: [],
    datasourceMappingOverrides: {},
    customDatasourceMappings: [],
  });
  const readConfigState = () => defaultConfigState();
  const writeConfigState = () => {};
  let configState = defaultConfigState();
  const nowText = () => {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  };
  const datasourceResources = Array.isArray(configPageData.datasourceResources)
    ? configPageData.datasourceResources
    : [];
  const projectSpaceOptions = configPageData.projectSpaceOptions || { source: [], target: [] };
  const datasourceOptions = configPageData.datasourceOptions || { source: [], target: [] };
  const projectSpaceMeta = [...(projectSpaceOptions.source || []), ...(projectSpaceOptions.target || [])]
    .reduce((acc, item) => {
      acc[String(item.id)] = item;
      return acc;
    }, {});
  const datasourceOptionMeta = [...(datasourceOptions.source || []), ...(datasourceOptions.target || [])]
    .reduce((acc, item) => {
      acc[String(item.id)] = item;
      return acc;
    }, {});
  const datasourceSourceMeta = datasourceResources
    .filter((item) => item.env_type === "test")
    .reduce((acc, item) => {
      const key = String(item.id || "");
      if (key && !acc[key]) {
        acc[key] = {
          name: item.name || "",
          type: item.type || "",
          projectSpace: item.project_space || "",
          sourceModule: item.source_module || "",
          connectivityStatus: item.connectivity_status || "unknown",
        };
      }
      return acc;
    }, {});
  const connectivityText = (status) => {
    if (status === "connected") return "正常";
    if (status === "failed") return "失败";
    return "未知";
  };
  const connectivityClass = (status) => {
    if (status === "connected") return "已发布";
    if (status === "failed") return "失败";
    return "未发布";
  };
  const postForm = async (url, payload) => {
    const body = new URLSearchParams();
    Object.entries(payload).forEach(([key, value]) => {
      body.set(key, value == null ? "" : String(value));
    });
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body,
    });
    return resp.json();
  };
  const scrollToSection = (sectionId) => {
    if (!sectionId) return;
    const section = document.getElementById(sectionId);
    if (!section) return;
    if (window.location.hash !== `#${sectionId}`) {
      window.history.replaceState(null, "", `#${sectionId}`);
    }
    section.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const getSectionAnchor = (element, fallbackId = "") => {
    const section = element?.closest("section[id]");
    return section?.id || fallbackId;
  };


  const loadCaptcha = async () => {
    const button = document.querySelector("[data-action='refresh-captcha'].captcha-image");
    const keyInput = document.querySelector("[data-captcha-key]");
    const publicKeyInput = document.querySelector("[data-login-public-key]");
    if (!button || !keyInput) return;

    button.textContent = "加载中";
    try {
      const resp = await fetch("/auth/captcha", { cache: "no-store" });
      const payload = await resp.json();
      if (!payload.success) throw new Error(payload.message || "验证码加载失败");
      keyInput.value = payload.key;
      if (publicKeyInput) publicKeyInput.value = payload.public_key || "";
      button.innerHTML = `<img alt="验证码" src="data:${payload.content_type};base64,${payload.image_base64}">`;
    } catch (error) {
      button.textContent = "重试";
      showToast(error.message || "验证码加载失败");
    }
  };

  document.querySelectorAll("[data-login-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const plainInput = form.querySelector("[data-login-password]");
      const cipherInput = form.querySelector("[data-password-ciphertext]");
      const publicKeyInput = form.querySelector("[data-login-public-key]");
      const plainPassword = plainInput?.value || "";
      const publicKey = publicKeyInput?.value || "";

      if (!plainPassword || !cipherInput || !publicKey || !window.sm2?.doEncrypt) {
        event.preventDefault();
        showToast("密码加密组件未就绪，请刷新验证码后重试");
        return;
      }

      try {
        cipherInput.value = `04${window.sm2.doEncrypt(plainPassword, publicKey, 0)}`;
      } catch (error) {
        event.preventDefault();
        cipherInput.value = "";
        showToast("密码加密失败，请刷新页面后重试");
      }
    });
  });

  document.querySelectorAll("[data-action='refresh-captcha']").forEach((button) => {
    button.addEventListener("click", loadCaptcha);
  });
  loadCaptcha();

  document.querySelectorAll("[data-action='load-git-branches']").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-git-binding-row]");
      const repoInput = row?.querySelector("input[name='repo_url']");
      const branchSelect = row?.querySelector("[data-git-branch-select]");
      const repoUrl = repoInput?.value?.trim() || "";
      if (!repoUrl || !branchSelect) {
        showToast("请先填写仓库地址");
        return;
      }

      button.disabled = true;
      try {
        const body = new URLSearchParams({ repo_url: repoUrl });
        const resp = await fetch("/config/git/branches", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body,
        });
        const payload = await resp.json();
        if (!payload.success) throw new Error(payload.message || "读取分支失败");
        const currentBranch = branchSelect.dataset.currentBranch || branchSelect.value || "";
        const preferredBranch = payload.branches.includes(currentBranch)
          ? currentBranch
          : (payload.branches.includes("main") ? "main" : (payload.branches.includes("master") ? "master" : payload.branches[0]));
        branchSelect.innerHTML = payload.branches.map((branch) => {
          const escaped = escapeHtml(branch);
          const selected = branch === preferredBranch ? " selected" : "";
          return `<option value="${escaped}"${selected}>${escaped}</option>`;
        }).join("");
        if (preferredBranch) {
          branchSelect.value = preferredBranch;
          branchSelect.dataset.currentBranch = preferredBranch;
        }
        showToast(`已读取 ${payload.branches.length} 个分支`);
      } catch (error) {
        showToast(error.message || "读取分支失败");
      } finally {
        button.disabled = false;
      }
    });
  });

  const setGitBindingRowEditing = (row, editing) => {
    row.dataset.editing = editing ? "1" : "";
    row.querySelector("input[name='repo_url']")?.toggleAttribute("disabled", !editing);
    row.querySelector("[data-git-branch-select]")?.toggleAttribute("disabled", !editing);
    row.querySelector("[data-action='edit-git-binding']")?.toggleAttribute("hidden", editing);
    row.querySelector("[data-action='load-git-branches']")?.toggleAttribute("hidden", !editing);
    row.querySelector("[data-action='cancel-git-binding']")?.toggleAttribute("hidden", !editing);
    row.querySelector("form button[type='submit']")?.toggleAttribute("hidden", !editing);
  };
  const resetGitBindingRow = (row) => {
    const repoInput = row.querySelector("input[name='repo_url']");
    const branchSelect = row.querySelector("[data-git-branch-select]");
    if (repoInput) {
      repoInput.value = row.dataset.initialRepoUrl || "";
    }
    if (branchSelect) {
      const originalBranch = row.dataset.initialBranch || branchSelect.dataset.currentBranch || branchSelect.value || "";
      branchSelect.value = originalBranch;
      branchSelect.dataset.currentBranch = originalBranch;
    }
    setGitBindingRowEditing(row, false);
  };

  const appendTemplateRow = (templateId, containerSelector, options = {}) => {
    const {
      emptySelector = "",
      successMessage = "",
      prepend = false,
    } = options;
    const template = document.getElementById(templateId);
    const container = document.querySelector(containerSelector);
    if (!template || !container) return;
    if (emptySelector) document.querySelector(emptySelector)?.remove();
    const row = template.content.firstElementChild.cloneNode(true);
    const firstPlaceholder = container.querySelector("[data-placeholder-row]");
    const anchor = prepend
      ? Array.from(container.children).find((child) => !child.hasAttribute("data-placeholder-row"))
      : firstPlaceholder;
    if (anchor) {
      container.insertBefore(row, anchor);
    } else {
      container.appendChild(row);
    }
    if (successMessage) showToast(successMessage);
    return row;
  };

  document.querySelector("[data-action='add-project-mapping-row']")?.addEventListener("click", () => {
    const row = appendTemplateRow(
      "project-mapping-row-template",
      "[data-project-mapping-list]",
      {
        successMessage: "已新增一条项目空间映射",
        prepend: true,
      }
    );
    if (row) {
      const tableCard = row.closest(".table-card");
      if (tableCard) getTableState(tableCard).page = 1;
      setProjectRowEditing(row, true);
      row.querySelector("[data-field='source_space']")?.focus();
      renderTable(tableCard);
      updateConfigStats(document.querySelector("[data-config-stats='project-mapping']"));
      scrollToSection(getSectionAnchor(row, "section-project-mappings"));
    }
  });

  document.querySelector("[data-action='add-datasource-row']")?.addEventListener("click", () => {
    const row = appendTemplateRow(
      "datasource-row-template",
      "[data-datasource-list]",
      {
        successMessage: "已新增一条数据源映射",
        prepend: true,
      }
    );
    if (row) {
      const tableCard = row.closest(".table-card");
      if (tableCard) getTableState(tableCard).page = 1;
      row.querySelector("[data-datasource-source]")?.focus();
      renderTable(tableCard);
      updateConfigStats(document.querySelector("[data-config-stats='datasource-mapping']"));
      scrollToSection(getSectionAnchor(row, "section-datasource-mappings"));
    }
  });

  const defaultPageSizeOptions = [10, 20, 30, 40, 50];
  const getPageSizeOptions = (pagination) => {
    const values = String(pagination?.dataset.pageSizeOptions || "")
      .split(",")
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item) && item > 0);
    return values.length ? values : defaultPageSizeOptions;
  };

  const getTableState = (tableCard) => {
    if (!tableCard.tableState) {
      const pagination = tableCard.querySelector("[data-pagination]");
      const activeSort = tableCard.querySelector(".sort-header.active");
      tableCard.tableState = {
        page: 1,
        pageSize: Number(pagination?.dataset.pageSize || 10),
        sortKey: activeSort?.dataset.sortKey || "",
        sortType: activeSort?.dataset.sortType || "text",
        sortDirection: activeSort?.classList.contains("asc") ? "asc" : "desc",
      };
    }
    return tableCard.tableState;
  };

  const readFilters = (scope) => ({
    keyword: (scope?.querySelector("[data-filter='keyword']")?.value || "").trim().toLowerCase(),
    module: scope?.querySelector("[data-filter='module']")?.value || "",
    status: scope?.querySelector("[data-filter='status']")?.value || "",
    start: scope?.querySelector("[data-filter='start']")?.value || "",
    end: scope?.querySelector("[data-filter='end']")?.value || "",
  });

  const rowMatchesFilters = (row, filters, includeStatus = true) => {
    const rowKeyword = (row.dataset.keyword || "").toLowerCase();
    const rowDate = row.dataset.start || "";
    return (!filters.keyword || rowKeyword.includes(filters.keyword))
      && (!filters.module || row.dataset.module === filters.module)
      && (!includeStatus || !filters.status || row.dataset.status === filters.status)
      && (!filters.start || rowDate >= filters.start)
      && (!filters.end || rowDate <= `${filters.end} 23:59:59`);
  };

  const sortRows = (tableCard, rows) => {
    const state = getTableState(tableCard);
    if (!state.sortKey) return rows;

    const valueOf = (row) => {
      const dataKey = `sort${state.sortKey.charAt(0).toUpperCase()}${state.sortKey.slice(1)}`;
      return row.dataset[dataKey] || "";
    };
    const sortedRows = [...rows].sort((left, right) => {
      let leftValue = valueOf(left);
      let rightValue = valueOf(right);
      if (state.sortType === "date") {
        leftValue = Date.parse(String(leftValue).replace(" ", "T")) || 0;
        rightValue = Date.parse(String(rightValue).replace(" ", "T")) || 0;
      }
      const result = state.sortType === "date"
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue), "zh-Hans-CN", { numeric: true });
      return state.sortDirection === "asc" ? result : -result;
    });
    const tbody = tableCard.querySelector("tbody");
    const placeholder = tbody?.querySelector("[data-placeholder-row]");
    sortedRows.forEach((row) => {
      if (!tbody) return;
      if (placeholder) {
        tbody.insertBefore(row, placeholder);
      } else {
        tbody.appendChild(row);
      }
    });
    return sortedRows;
  };

  const syncSortHeaders = (tableCard) => {
    const state = getTableState(tableCard);
    tableCard.querySelectorAll(".sort-header").forEach((item) => {
      const active = item.dataset.sortKey === state.sortKey;
      item.classList.toggle("active", active);
      item.classList.toggle("asc", active && state.sortDirection === "asc");
      item.classList.toggle("desc", active && state.sortDirection === "desc");
      const icon = item.querySelector(".sort-mark");
      if (icon) icon.textContent = active ? (state.sortDirection === "asc" ? "↑" : "↓") : "↕";
    });
  };

  const updatePagination = (tableCard, visibleCount) => {
    const pagination = tableCard.querySelector("[data-pagination]");
    if (!pagination) return;

    const state = getTableState(tableCard);
    const pageSize = state.pageSize;
    const pageCount = Math.max(1, Math.ceil(visibleCount / pageSize));
    state.page = Math.min(Math.max(1, state.page), pageCount);
    const total = pagination.querySelector("[data-pagination-total]");
    const buttons = pagination.querySelector("[data-page-buttons]");
    const prev = pagination.querySelector("[data-page-action='prev']");
    const next = pagination.querySelector("[data-page-action='next']");
    const sizeSelect = pagination.querySelector("[data-page-size-select]");
    const jumpInput = pagination.querySelector("[data-page-jump]");
    const pageSizeOptions = getPageSizeOptions(pagination);
    if (total) total.textContent = `共 ${visibleCount} 条`;
    if (buttons) {
      buttons.innerHTML = Array.from({ length: pageCount }, (_, index) => {
        const page = index + 1;
        return `<button type="button" data-page-number="${page}" class="${page === state.page ? "active" : ""}">${page}</button>`;
      }).join("");
    }
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= pageCount;
    if (sizeSelect) {
      sizeSelect.innerHTML = pageSizeOptions.map((size) => (
        `<option value="${size}" ${size === state.pageSize ? "selected" : ""}>${size} 条/页</option>`
      )).join("");
    }
    if (jumpInput) jumpInput.value = String(state.page);
  };

  const updateTaskStats = (scope) => {
    if (scope.dataset.filterScope !== "tasks") return;

    const content = scope.closest(".content");
    const tableCard = content.querySelector(".table-card");
    const filters = readFilters(scope);
    const rows = Array.from(tableCard.querySelectorAll("[data-row]"));
    const matchedRows = rows.filter((row) => rowMatchesFilters(row, filters, false));
    const counts = {
      total: matchedRows.length,
      unpublished: matchedRows.filter((row) => row.dataset.status === "未发布").length,
      published: matchedRows.filter((row) => row.dataset.status === "已发布").length,
      failed: matchedRows.filter((row) => row.dataset.status === "发布失败").length,
    };

    Object.entries(counts).forEach(([key, value]) => {
      const target = content.querySelector(`[data-stat-value='${key}']`);
      if (target) target.textContent = String(value);
    });
  };

  const renderTable = (tableCard, scope = tableCard.closest(".content")?.querySelector("[data-filter-scope]")) => {
    if (!tableCard) return;

    const state = getTableState(tableCard);
    const rows = sortRows(tableCard, Array.from(tableCard.querySelectorAll("[data-row]")));
    const filters = readFilters(scope);
    const matchedRows = rows.filter((row) => rowMatchesFilters(row, filters));
    updatePagination(tableCard, matchedRows.length);

    const startIndex = (state.page - 1) * state.pageSize;
    const visiblePageRows = matchedRows.slice(startIndex, startIndex + state.pageSize);
    const visibleRows = new Set(visiblePageRows);
    rows.forEach((row) => {
      row.hidden = !visibleRows.has(row);
    });

    const placeholderRows = Array.from(tableCard.querySelectorAll("[data-placeholder-row]"));
    const minRows = Number(tableCard.dataset.minRows || 0);
    const fillerCount = Math.max(0, minRows - visiblePageRows.length);
    placeholderRows.forEach((row, index) => {
      row.hidden = index >= fillerCount;
    });

    const emptyState = tableCard.querySelector("[data-empty-state]");
    if (emptyState) emptyState.hidden = matchedRows.length !== 0;
    syncSortHeaders(tableCard);
    if (scope) updateTaskStats(scope);
    return matchedRows.length;
  };

  const applyFilter = (scope, options = {}) => {
    const tableCard = scope.closest(".content").querySelector(".table-card");
    if (!tableCard) return;

    getTableState(tableCard).page = 1;
    const visibleCount = renderTable(tableCard, scope);
    if (!options.silent) showToast(`已筛选出 ${visibleCount} 条记录`);
  };

  document.querySelectorAll(".sort-header").forEach((button) => {
    button.addEventListener("click", () => {
      const tableCard = button.closest(".table-card");
      const state = getTableState(tableCard);
      const sameKey = state.sortKey === button.dataset.sortKey;
      state.sortKey = button.dataset.sortKey;
      state.sortType = button.dataset.sortType || "text";
      state.sortDirection = sameKey && state.sortDirection === "asc" ? "desc" : "asc";
      state.page = 1;
      renderTable(tableCard);
    });
  });

  document.querySelectorAll("[data-pagination]").forEach((pagination) => {
    const tableCard = pagination.closest(".table-card");
    if (!tableCard) return;

    pagination.addEventListener("click", (event) => {
      const button = event.target.closest("[data-page-number], [data-page-action]");
      if (!button || button.disabled) return;

      const state = getTableState(tableCard);
      if (button.dataset.pageNumber) {
        state.page = Number(button.dataset.pageNumber);
      } else if (button.dataset.pageAction === "prev") {
        state.page -= 1;
      } else if (button.dataset.pageAction === "next") {
        state.page += 1;
      }
      renderTable(tableCard);
    });

    pagination.addEventListener("change", (event) => {
      if (!event.target.matches("[data-page-size-select]")) return;
      const state = getTableState(tableCard);
      state.pageSize = Number(event.target.value || 10);
      state.page = 1;
      pagination.dataset.pageSize = String(state.pageSize);
      renderTable(tableCard);
    });

    const jumpToPage = (input) => {
      const state = getTableState(tableCard);
      const page = Number(input.value || 1);
      state.page = Number.isFinite(page) ? page : 1;
      renderTable(tableCard);
    };

    pagination.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.matches("[data-page-jump]")) {
        jumpToPage(event.target);
      }
    });
    pagination.addEventListener("blur", (event) => {
      if (event.target.matches("[data-page-jump]")) jumpToPage(event.target);
    }, true);
  });

  document.querySelectorAll(".table-card").forEach((tableCard) => {
    if (tableCard.querySelector("[data-row]") || tableCard.querySelector("[data-pagination]")) renderTable(tableCard);
  });

  const projectStatusText = (enabled, targetSpace) => {
    if (!enabled) return "停用";
    if (!targetSpace) return "待配置";
    return "启用";
  };
  const datasourceStatusText = (enabled, target) => {
    if (!target) return "待配置";
    return enabled ? "启用" : "停用";
  };
  const syncProjectRowKeyword = (row) => {
    row.dataset.keyword = [
      row.dataset.sourceSpace || "",
      row.dataset.targetSpace || "",
      row.querySelector("[data-project-mapping-status]")?.textContent || "",
      row.querySelector("[data-project-match-rule]")?.textContent || "",
    ].join(" ");
  };
  const ensureProjectStandardActions = (row) => {
    const cell = row.querySelector(".config-row-actions");
    if (!cell) return;
    cell.innerHTML = `
      <button class="link-button" type="button" data-action="edit-project-mapping">编辑</button>
      <button class="link-button" type="button" data-action="toggle-project-mapping">停用</button>
      <button class="link-button" type="button" data-action="save-project-mapping" hidden>保存</button>
      <button class="link-button" type="button" data-action="cancel-project-mapping" hidden>取消</button>
    `;
  };
  const setProjectRowEditing = (row, editing) => {
    const hasExistingSource = Boolean(row.dataset.sourceProjectSpaceId);
    const targetSelect = row.querySelector("[data-field='target_space']");
    row.querySelectorAll(".cell-editor").forEach((input) => {
      if (hasExistingSource && input.dataset.field === "source_space") {
        input.hidden = true;
      } else {
        input.hidden = !editing;
      }
    });
    row.querySelectorAll(".cell-display").forEach((display) => {
      const editor = display.parentElement?.querySelector(".cell-editor");
      if (!editor) return;
      if (hasExistingSource && editor.dataset.field === "source_space") {
        display.hidden = false;
      } else {
        display.hidden = editing;
      }
    });
    row.querySelector("[data-action='edit-project-mapping']")?.toggleAttribute("hidden", editing);
    row.querySelector("[data-action='toggle-project-mapping']")?.toggleAttribute("hidden", editing);
    row.querySelector("[data-action='save-project-mapping']")?.toggleAttribute("hidden", !editing);
    row.querySelector("[data-action='cancel-project-mapping']")?.toggleAttribute("hidden", !editing);
    row.dataset.editing = editing ? "1" : "";
    if (editing) {
      if (targetSelect) {
        row.dataset.originalTargetProjectSpaceId = row.dataset.targetProjectSpaceId || targetSelect.value || "";
        targetSelect.value = "";
        targetSelect.focus();
      }
    } else if (targetSelect && row.dataset.originalTargetProjectSpaceId) {
      targetSelect.value = row.dataset.originalTargetProjectSpaceId;
    }
  };
  const applyProjectRowState = (row, state = {}) => {
    if (!row) return;
    const sourceSpace = state.sourceSpace ?? row.dataset.sourceSpace ?? "";
    const targetSpace = state.targetSpace ?? row.dataset.targetSpace ?? "";
    const sourceProjectSpaceId = String(state.sourceProjectSpaceId ?? row.dataset.sourceProjectSpaceId ?? "");
    const targetProjectSpaceId = String(state.targetProjectSpaceId ?? row.dataset.targetProjectSpaceId ?? "");
    const enabled = state.enabled ?? (row.dataset.status !== "disabled");
    const matchRule = state.matchRule ?? row.querySelector("[data-project-match-rule]")?.textContent ?? "same_name";
    const lastSyncedAt = state.lastSyncedAt ?? row.querySelector("[data-project-last-synced]")?.textContent ?? "-";
    const sourceOption = projectSpaceMeta[sourceProjectSpaceId] || null;
    const targetOption = projectSpaceMeta[targetProjectSpaceId] || null;
    row.dataset.sourceSpace = sourceSpace;
    row.dataset.targetSpace = targetSpace;
    row.dataset.sourceProjectSpaceId = sourceProjectSpaceId;
    row.dataset.targetProjectSpaceId = targetProjectSpaceId;
    row.dataset.status = enabled ? "confirmed" : "disabled";
    row.dataset.mappingEnabled = enabled ? "1" : "0";
    row.dataset.sourceType = row.dataset.sourceType || (sourceOption?.project_type || "").toLowerCase();
    const sourceSelect = row.querySelector("[data-field='source_space']");
    const targetSelect = row.querySelector("[data-field='target_space']");
    if (sourceSelect) sourceSelect.value = sourceProjectSpaceId;
    if (targetSelect) targetSelect.value = targetProjectSpaceId;
    const typeLabel = row.querySelector("[data-project-type-label]");
    if (typeLabel) {
      const sourceType = String(sourceOption?.project_type || "").toLowerCase();
      typeLabel.textContent = sourceType === "offline" ? "离线" : (sourceType ? "实时" : "待带出");
      typeLabel.className = `tag ${sourceType === "offline" ? "green" : "plain"}`;
    }
    row.querySelectorAll(".cell-display").forEach((display) => {
      const field = display.parentElement?.querySelector("[data-field]")?.dataset.field;
      if (field === "source_space") display.textContent = sourceOption?.name || sourceSpace || "请选择测试项目空间";
      if (field === "target_space") display.textContent = targetOption?.name || targetSpace || "未配置";
    });
    const statusEl = row.querySelector("[data-project-mapping-status]");
    if (statusEl) {
      statusEl.textContent = projectStatusText(enabled, targetSpace);
      statusEl.className = `status ${enabled && targetSpace ? "已发布" : "未发布"}`;
    }
    const matchRuleEl = row.querySelector("[data-project-match-rule]");
    if (matchRuleEl) matchRuleEl.textContent = matchRule;
    const lastSyncedEl = row.querySelector("[data-project-last-synced]");
    if (lastSyncedEl) lastSyncedEl.textContent = lastSyncedAt;
    const toggleBtn = row.querySelector("[data-action='toggle-project-mapping']");
    if (toggleBtn) toggleBtn.textContent = enabled ? "停用" : "启用";
    syncProjectRowKeyword(row);
    setProjectRowEditing(row, false);
  };
  const projectSourceExists = (sourceProjectSpaceId, currentRow) => Array.from(document.querySelectorAll("[data-project-mapping-row]"))
    .filter((row) => row !== currentRow)
    .some((row) => (row.dataset.sourceProjectSpaceId || "") === sourceProjectSpaceId);
  const persistProjectOverride = (row, isCustom = false) => {
    const record = {
      id: row.dataset.projectMappingId,
      sourceSpace: row.dataset.sourceSpace || "",
      targetSpace: row.dataset.targetSpace || "",
      enabled: row.dataset.status !== "disabled",
      matchRule: row.querySelector("[data-project-match-rule]")?.textContent || "manual",
      lastSyncedAt: row.querySelector("[data-project-last-synced]")?.textContent || nowText(),
      sourceEnv: row.dataset.sourceEnv || "测试环境",
      targetEnv: row.dataset.targetEnv || "生产环境",
    };
    if (isCustom) {
      configState.customProjectMappings = configState.customProjectMappings
        .filter((item) => item.id !== record.id)
        .concat(record);
    } else {
      configState.projectMappingOverrides[record.id] = record;
    }
    writeConfigState(configState);
  };
  const removeProjectState = (row) => {
    const rowId = row.dataset.projectMappingId;
    delete configState.projectMappingOverrides[rowId];
    configState.customProjectMappings = configState.customProjectMappings.filter((item) => item.id !== rowId);
    writeConfigState(configState);
  };

  const syncDatasourceRowKeyword = (row) => {
    row.dataset.keyword = [
      row.dataset.source || "",
      row.dataset.type || "",
      row.querySelector("[data-datasource-target]")?.value || "",
      row.querySelector("[data-datasource-status]")?.textContent || "",
    ].join(" ");
  };
  const ensureDatasourceStandardActions = (row) => {
    const cell = row.querySelector(".config-row-actions");
    if (!cell) return;
    cell.innerHTML = `
      <button class="link-button" type="button" data-action="edit-datasource">编辑</button>
      <button class="link-button" type="button" data-action="toggle-datasource">停用</button>
      <button class="link-button" type="button" data-action="save-datasource" hidden>保存</button>
      <button class="link-button" type="button" data-action="cancel-datasource" hidden>取消</button>
      <button class="link-button danger-link compact" type="button" data-action="clear-datasource" hidden>清空</button>
    `;
  };
  const setDatasourceRowEditing = (row, editing) => {
    row.dataset.editing = editing ? "1" : "";
    row.querySelector("[data-datasource-source]")?.toggleAttribute("disabled", !editing && Boolean(row.dataset.sourceResourceId));
    row.querySelector("[data-datasource-target]")?.toggleAttribute("disabled", !editing);
    row.querySelector("[data-action='edit-datasource']")?.toggleAttribute("hidden", editing);
    row.querySelector("[data-action='toggle-datasource']")?.toggleAttribute("hidden", editing);
    row.querySelector("[data-action='save-datasource']")?.toggleAttribute("hidden", !editing);
    row.querySelector("[data-action='cancel-datasource']")?.toggleAttribute("hidden", !editing);
    row.querySelector("[data-action='clear-datasource']")?.toggleAttribute("hidden", !editing);
  };
  const applyDatasourceRowState = (row, state = {}) => {
    if (!row) return;
    const source = state.source ?? row.dataset.source ?? "";
    const type = state.type ?? row.dataset.type ?? "";
    const target = state.target ?? row.dataset.target ?? "";
    const sourceResourceId = String(state.sourceResourceId ?? row.dataset.sourceResourceId ?? "");
    const targetResourceId = String(state.targetResourceId ?? row.dataset.targetResourceId ?? "");
    const connectivityStatus = state.connectivityStatus ?? row.dataset.connectivityStatus ?? "unknown";
    const lastSyncedAt = state.lastSyncedAt ?? row.querySelector("[data-datasource-last-synced]")?.textContent ?? "-";
    row.dataset.source = source;
    row.dataset.type = type;
    row.dataset.target = target;
    row.dataset.sourceResourceId = sourceResourceId;
    row.dataset.targetResourceId = targetResourceId;
    row.dataset.connectivityStatus = connectivityStatus;
    const sourceSelect = row.querySelector("[data-datasource-source]");
    if (sourceSelect) {
      sourceSelect.value = sourceResourceId;
    }
    const targetSelect = row.querySelector("[data-datasource-target]");
    if (targetSelect) targetSelect.value = targetResourceId;
    const typeEl = row.querySelector("[data-datasource-type]");
    if (typeEl) typeEl.textContent = type || "待带出";
    const statusEl = row.querySelector("[data-datasource-status]");
    if (statusEl) {
      const enabled = !["disabled", "0", "", "false", "False"].includes(String(row.dataset.mappingEnabled ?? "1"));
      statusEl.textContent = datasourceStatusText(enabled, target);
      statusEl.className = `status ${enabled && target ? "已发布" : "未发布"}`;
    }
    const connectivityEl = row.querySelector("[data-datasource-connectivity]");
    if (connectivityEl) {
      connectivityEl.textContent = connectivityText(connectivityStatus);
      connectivityEl.className = `status ${connectivityClass(connectivityStatus)}`;
    }
    const syncedEl = row.querySelector("[data-datasource-last-synced]");
    if (syncedEl) syncedEl.textContent = lastSyncedAt;
    const toggleBtn = row.querySelector("[data-action='toggle-datasource']");
    if (toggleBtn) toggleBtn.textContent = row.dataset.mappingEnabled === "1" && target ? "停用" : "启用";
    syncDatasourceRowKeyword(row);
    setDatasourceRowEditing(row, row.dataset.editing === "1");
    // 按类型过滤目标数据源下拉框
    const _targetSelect = row.querySelector("[data-datasource-target]");
    const _sourceType = (row.dataset.type || "").toLowerCase();
    if (_targetSelect && _sourceType) {
      Array.from(_targetSelect.options).forEach((opt) => {
        if (!opt.value) return;
        const optionMeta = datasourceOptionMeta[opt.value] || {};
        opt.hidden = optionMeta.type && optionMeta.type.toLowerCase() !== _sourceType;
      });
    }
  };
  const datasourceSourceExists = (sourceResourceId, currentRow) => Array.from(document.querySelectorAll("[data-datasource-row]"))
    .filter((row) => row !== currentRow)
    .some((row) => (row.dataset.sourceResourceId || "") === sourceResourceId);
  const updateConfigStats = (statsContainer) => {
    if (!statsContainer) return;
    const tableCard = statsContainer.closest(".table-card");
    if (!tableCard) return;
    const rows = Array.from(tableCard.querySelectorAll("[data-row]"));
    const total = rows.length;
    const isProject = statsContainer.dataset.configStats === "project-mapping";
    const enabled = rows.filter((row) => {
      if (isProject) {
        // 项目空间映射：status === "confirmed" 且 target_space 有值
        return row.dataset.status === "confirmed" && row.dataset.targetSpace;
      }
      const enabledVal = row.dataset.mappingEnabled;
      const target = row.dataset.target;
      return (enabledVal === "1" || enabledVal === "true") && target;
    }).length;
    const pending = rows.filter((row) => {
      if (isProject) {
        return !row.dataset.targetSpace || row.dataset.status === "disabled";
      }
      return !row.dataset.target;
    }).length;
    const connected = rows.filter((row) => {
      if (isProject) return row.dataset.sourceType !== "offline";
      return row.dataset.connectivityStatus === "connected";
    }).length;
    const failed = rows.filter((row) => !isProject && row.dataset.connectivityStatus === "failed").length;
    const stream = isProject ? rows.filter((row) => row.dataset.sourceType !== "offline").length : 0;
    const offline = isProject ? rows.filter((row) => row.dataset.sourceType === "offline").length : 0;
    const counters = isProject ? { total, enabled, pending, offline, stream } : { total, enabled, pending, connected, failed };
    Object.entries(counters).forEach(([key, value]) => {
      const el = statsContainer.querySelector(`[data-stat-value="${key}"]`);
      if (el) el.textContent = String(value);
    });
  };
  const persistDatasourceOverride = (row, isCustom = false) => {
    const record = {
      id: row.dataset.datasourceRowId,
      source: row.dataset.source || "",
      type: row.dataset.type || "",
      target: row.querySelector("[data-datasource-target]")?.value || "",
      connected: row.querySelector("[data-datasource-connectivity]")?.textContent === "✓",
      lastSyncedAt: row.querySelector("[data-datasource-last-synced]")?.textContent || nowText(),
    };
    if (isCustom) {
      configState.customDatasourceMappings = configState.customDatasourceMappings
        .filter((item) => item.id !== record.id)
        .concat(record);
    } else {
      configState.datasourceMappingOverrides[record.id] = record;
    }
    writeConfigState(configState);
  };
  const removeDatasourceState = (row) => {
    const rowId = row.dataset.datasourceRowId;
    delete configState.datasourceMappingOverrides[rowId];
    configState.customDatasourceMappings = configState.customDatasourceMappings.filter((item) => item.id !== rowId);
    writeConfigState(configState);
  };

  document.querySelectorAll("[data-project-mapping-row]").forEach((row) => {
    applyProjectRowState(row);
    // 页面初始化时按 sourceType 过滤生产环境下拉框
    const targetSelect = row.querySelector("[data-field='target_space']");
    const sourceType = (row.dataset.sourceType || "").toLowerCase();
    if (targetSelect && sourceType) {
      Array.from(targetSelect.options).forEach((opt) => {
        if (!opt.value) return;
        const optionMeta = projectSpaceMeta[opt.value] || {};
        opt.hidden = optionMeta.project_type && sourceType && optionMeta.project_type !== sourceType;
      });
    }
  });
  document.querySelectorAll("[data-datasource-row]").forEach((row) => {
    row.dataset.datasourceRowId = row.dataset.datasourceRowId || row.dataset.datasourceRowId || row.dataset.sourceResourceId || `row-${Date.now()}`;
    row.dataset.mappingEnabled = row.dataset.mappingEnabled || "0";
    applyDatasourceRowState(row, {
      source: row.dataset.source,
      type: row.dataset.type,
      target: row.dataset.target,
      sourceResourceId: row.dataset.sourceResourceId,
      targetResourceId: row.dataset.targetResourceId,
      connectivityStatus: row.dataset.connectivityStatus || (
        row.querySelector("[data-datasource-connectivity]")?.textContent === "正常" ? "connected" : "unknown"
      ),
    });
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;

    if (target.matches("[data-field='source_space']")) {
      const row = target.closest("[data-project-mapping-row]");
      const sourceMeta = projectSpaceMeta[target.value] || {};
      if (row) {
        const typeLabel = row.querySelector("[data-project-type-label]");
        row.dataset.sourceProjectSpaceId = target.value || "";
        row.dataset.sourceSpace = sourceMeta.name || target.selectedOptions?.[0]?.textContent || "";
        row.dataset.sourceType = (sourceMeta.project_type || "").toLowerCase();
        if (typeLabel) {
          const sourceType = String(sourceMeta.project_type || "").toLowerCase();
          typeLabel.textContent = sourceType === "offline" ? "离线" : (sourceType ? "实时" : "待带出");
          typeLabel.className = `tag ${sourceType === "offline" ? "green" : "plain"}`;
        }
        // 按类型过滤生产环境项目空间下拉框
        const targetSelect = row.querySelector("[data-field='target_space']");
        if (targetSelect) {
          const selectedValue = targetSelect.value;
          Array.from(targetSelect.options).forEach((opt) => {
            if (!opt.value) return;
            const optionMeta = projectSpaceMeta[opt.value] || {};
            opt.hidden = optionMeta.project_type && row.dataset.sourceType && optionMeta.project_type !== row.dataset.sourceType;
          });
          if (selectedValue && Array.from(targetSelect.options).every((o) => o.hidden || o.value !== selectedValue)) {
            targetSelect.value = "";
          }
        }
      }
    }
    if (target.matches("[data-datasource-source]")) {
      const row = target.closest("[data-datasource-row]");
      const meta = datasourceSourceMeta[target.value] || {};
      if (row) {
        row.dataset.sourceResourceId = target.value || "";
        row.dataset.source = meta.name || target.selectedOptions?.[0]?.textContent || "";
        row.dataset.type = meta.type || "";
        row.dataset.connectivityStatus = meta.connectivityStatus || "unknown";
        row.querySelector("[data-datasource-type]").textContent = meta.type || "待带出";
        const connectivityEl = row.querySelector("[data-datasource-connectivity]");
        if (connectivityEl) {
          connectivityEl.textContent = connectivityText(row.dataset.connectivityStatus);
          connectivityEl.className = `status ${connectivityClass(row.dataset.connectivityStatus)}`;
        }
        // 按类型过滤目标数据源下拉框
        const targetSelect = row.querySelector("[data-datasource-target]");
        const selectedValue = targetSelect.value;
        Array.from(targetSelect.options).forEach((opt) => {
          if (!opt.value) return;
          const optionMeta = datasourceOptionMeta[opt.value] || {};
          opt.hidden = meta.type && optionMeta.type && optionMeta.type !== meta.type;
        });
        if (selectedValue && Array.from(targetSelect.options).every((o) => o.hidden || o.value !== selectedValue)) {
          targetSelect.value = "";
        }
        syncDatasourceRowKeyword(row);
      }
    }
    if (target.matches("[data-datasource-target]")) {
      const row = target.closest("[data-datasource-row]");
      if (row) {
        row.dataset.targetResourceId = target.value || "";
        row.dataset.target = datasourceOptionMeta[target.value]?.name || target.selectedOptions?.[0]?.textContent || "";
        if (!row.dataset.mappingEnabled) row.dataset.mappingEnabled = "1";
        syncDatasourceRowKeyword(row);
      }
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;

    const gitBindingRow = button.closest("[data-git-binding-row]");
    if (gitBindingRow && button.dataset.action === "edit-git-binding") {
      setGitBindingRowEditing(gitBindingRow, true);
      gitBindingRow.querySelector("input[name='repo_url']")?.focus();
      scrollToSection(getSectionAnchor(gitBindingRow, "section-git-bindings"));
      return;
    }
    if (gitBindingRow && button.dataset.action === "cancel-git-binding") {
      resetGitBindingRow(gitBindingRow);
      scrollToSection(getSectionAnchor(gitBindingRow, "section-git-bindings"));
      return;
    }

    const projectRow = button.closest("[data-project-mapping-row]");
    if (projectRow && button.dataset.action === "edit-project-mapping") {
      setProjectRowEditing(projectRow, true);
      scrollToSection(getSectionAnchor(projectRow, "section-project-mappings"));
      return;
    }
    if (projectRow && button.dataset.action === "cancel-project-mapping") {
      const rowId = projectRow.dataset.projectMappingId;
      if (!rowId || rowId.startsWith("local-")) {
        projectRow.remove();
      } else {
        applyProjectRowState(projectRow, {
          sourceProjectSpaceId: projectRow.dataset.sourceProjectSpaceId || "",
          targetProjectSpaceId: projectRow.dataset.targetProjectSpaceId || "",
          sourceSpace: projectRow.dataset.sourceSpace || "",
          targetSpace: projectRow.dataset.targetSpace || "",
          enabled: projectRow.dataset.status !== "disabled",
          matchRule: projectRow.querySelector("[data-project-match-rule]")?.textContent || "manual",
          lastSyncedAt: projectRow.querySelector("[data-project-last-synced]")?.textContent || "-",
        });
      }
      updateConfigStats(document.querySelector("[data-config-stats='project-mapping']"));
      renderTable(projectRow.closest(".table-card"));
      scrollToSection(getSectionAnchor(projectRow, "section-project-mappings"));
      return;
    }
    if (projectRow && button.dataset.action === "save-project-mapping") {
      const sourceProjectSpaceId = projectRow.querySelector("[data-field='source_space']")?.value || projectRow.dataset.sourceProjectSpaceId || "";
      const targetProjectSpaceId = projectRow.querySelector("[data-field='target_space']")?.value || projectRow.dataset.targetProjectSpaceId || "";
      if (!sourceProjectSpaceId || !targetProjectSpaceId) {
        showToast("请先选择测试和生产项目空间");
        return;
      }
      if (projectSourceExists(sourceProjectSpaceId, projectRow)) {
        showToast("该测试项目空间已存在映射，请勿重复配置");
        return;
      }
      button.disabled = true;
      try {
        const payload = await postForm("/config/project-mappings/save", {
          source_project_space_id: sourceProjectSpaceId,
          target_project_space_id: targetProjectSpaceId,
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "项目空间映射保存失败");
        }
        applyProjectRowState(projectRow, {
          sourceProjectSpaceId,
          targetProjectSpaceId,
          sourceSpace: projectSpaceMeta[sourceProjectSpaceId]?.name || "",
          targetSpace: projectSpaceMeta[targetProjectSpaceId]?.name || "",
          enabled: true,
          matchRule: "manual",
          lastSyncedAt: nowText(),
        });
        showToast(payload.message || "项目空间映射已保存");
        updateConfigStats(document.querySelector("[data-config-stats='project-mapping']"));
        scrollToSection(getSectionAnchor(projectRow, "section-project-mappings"));
      } catch (error) {
        showToast(error.message || "项目空间映射保存失败");
      } finally {
        button.disabled = false;
      }
      renderTable(projectRow.closest(".table-card"));
      return;
    }
    if (projectRow && button.dataset.action === "toggle-project-mapping") {
      const enabled = projectRow.dataset.status !== "disabled";
      button.disabled = true;
      try {
        const payload = await postForm("/config/project-mappings/toggle", {
          source_project_space_id: projectRow.dataset.sourceProjectSpaceId || "",
          enabled: enabled ? "0" : "1",
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "项目空间映射更新失败");
        }
        applyProjectRowState(projectRow, {
          sourceProjectSpaceId: projectRow.dataset.sourceProjectSpaceId || "",
          targetProjectSpaceId: projectRow.dataset.targetProjectSpaceId || "",
          sourceSpace: projectRow.dataset.sourceSpace || "",
          targetSpace: projectRow.dataset.targetSpace || "",
          enabled: !enabled,
          matchRule: projectRow.querySelector("[data-project-match-rule]")?.textContent || "manual",
          lastSyncedAt: nowText(),
        });
        showToast(payload.message || (!enabled ? "项目空间映射已启用" : "项目空间映射已停用"));
        updateConfigStats(document.querySelector("[data-config-stats='project-mapping']"));
        scrollToSection(getSectionAnchor(projectRow, "section-project-mappings"));
      } catch (error) {
        showToast(error.message || "项目空间映射更新失败");
      } finally {
        button.disabled = false;
      }
      return;
    }

    const datasourceRow = button.closest("[data-datasource-row]");
    if (datasourceRow && button.dataset.action === "edit-datasource") {
      setDatasourceRowEditing(datasourceRow, true);
      datasourceRow.querySelector("[data-datasource-target]")?.focus();
      scrollToSection(getSectionAnchor(datasourceRow, "section-datasource-mappings"));
      return;
    }
    if (datasourceRow && button.dataset.action === "cancel-datasource") {
      if (!datasourceRow.dataset.datasourceRowId) {
        datasourceRow.remove();
      } else {
        applyDatasourceRowState(datasourceRow, {
          sourceResourceId: datasourceRow.dataset.sourceResourceId || "",
          targetResourceId: datasourceRow.dataset.targetResourceId || "",
          source: datasourceRow.dataset.source || "",
          type: datasourceRow.dataset.type || "",
          target: datasourceRow.dataset.target || "",
          connectivityStatus: datasourceRow.dataset.connectivityStatus || "unknown",
          lastSyncedAt: datasourceRow.querySelector("[data-datasource-last-synced]")?.textContent || "-",
        });
        setDatasourceRowEditing(datasourceRow, false);
      }
      updateConfigStats(document.querySelector("[data-config-stats='datasource-mapping']"));
      renderTable(datasourceRow.closest(".table-card"));
      scrollToSection(getSectionAnchor(datasourceRow, "section-datasource-mappings"));
      return;
    }
    if (datasourceRow && button.dataset.action === "save-datasource") {
      const sourceResourceId = datasourceRow.dataset.sourceResourceId || datasourceRow.querySelector("[data-datasource-source]")?.value || "";
      const targetResourceId = datasourceRow.querySelector("[data-datasource-target]")?.value || "";
      const meta = datasourceSourceMeta[sourceResourceId] || {};
      const type = datasourceRow.dataset.type || meta.type || "";
      if (!sourceResourceId || !targetResourceId) {
        showToast("请先选择测试和生产环境数据源");
        return;
      }
      if (datasourceSourceExists(sourceResourceId, datasourceRow)) {
        showToast("该测试环境数据源已存在映射，请勿重复选择");
        return;
      }
      button.disabled = true;
      try {
        const payload = await postForm("/config/datasource-mappings/save", {
          source_resource_id: sourceResourceId,
          target_resource_id: targetResourceId,
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "数据源映射保存失败");
        }
        datasourceRow.dataset.mappingEnabled = "1";
        datasourceRow.dataset.datasourceRowId = datasourceRow.dataset.datasourceRowId || sourceResourceId;
        applyDatasourceRowState(datasourceRow, {
          sourceResourceId,
          targetResourceId,
          source: datasourceSourceMeta[sourceResourceId]?.name || datasourceRow.dataset.source || "",
          type,
          target: datasourceOptionMeta[targetResourceId]?.name || datasourceRow.dataset.target || "",
          connectivityStatus: payload.connectivity_status || datasourceSourceMeta[sourceResourceId]?.connectivityStatus || "unknown",
          lastSyncedAt: nowText(),
        });
        setDatasourceRowEditing(datasourceRow, false);
        updateConfigStats(datasourceRow.closest(".table-card")?.querySelector("[data-config-stats='datasource-mapping']"));
        showToast(payload.message || "数据源映射已保存");
        scrollToSection(getSectionAnchor(datasourceRow, "section-datasource-mappings"));
      } catch (error) {
        showToast(error.message || "数据源映射保存失败");
      } finally {
        button.disabled = false;
      }
      renderTable(datasourceRow.closest(".table-card"));
      return;
    }
    if (datasourceRow && button.dataset.action === "clear-datasource") {
      if (!datasourceRow.dataset.sourceResourceId) {
        datasourceRow.remove();
        updateConfigStats(document.querySelector("[data-config-stats='datasource-mapping']"));
        renderTable(datasourceRow.closest(".table-card"));
        return;
      }
      button.disabled = true;
      try {
        const payload = await postForm("/config/datasource-mappings/clear", {
          source_resource_id: datasourceRow.dataset.sourceResourceId || "",
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "清空数据源映射失败");
        }
        applyDatasourceRowState(datasourceRow, {
          sourceResourceId: datasourceRow.dataset.sourceResourceId || "",
          source: datasourceRow.dataset.source || "",
          type: datasourceRow.dataset.type || "",
          target: "",
          targetResourceId: "",
          connectivityStatus: datasourceSourceMeta[datasourceRow.dataset.sourceResourceId || ""]?.connectivityStatus || "unknown",
          lastSyncedAt: nowText(),
        });
        datasourceRow.dataset.mappingEnabled = "0";
        setDatasourceRowEditing(datasourceRow, false);
        updateConfigStats(datasourceRow.closest(".table-card")?.querySelector("[data-config-stats='datasource-mapping']"));
        showToast(payload.message || "已清空数据源映射");
        scrollToSection(getSectionAnchor(datasourceRow, "section-datasource-mappings"));
      } catch (error) {
        showToast(error.message || "清空数据源映射失败");
      } finally {
        button.disabled = false;
      }
      renderTable(datasourceRow.closest(".table-card"));
      return;
    }
    if (datasourceRow && button.dataset.action === "toggle-datasource") {
      const enabled = datasourceRow.dataset.mappingEnabled === "1";
      if (!datasourceRow.dataset.sourceResourceId) {
        showToast("请先保存数据源映射后再启用或停用");
        return;
      }
      button.disabled = true;
      try {
        const payload = await postForm("/config/datasource-mappings/toggle", {
          source_resource_id: datasourceRow.dataset.sourceResourceId || "",
          enabled: enabled ? "0" : "1",
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "数据源映射状态更新失败");
        }
        datasourceRow.dataset.mappingEnabled = enabled ? "0" : "1";
        applyDatasourceRowState(datasourceRow, {
          sourceResourceId: datasourceRow.dataset.sourceResourceId || "",
          targetResourceId: datasourceRow.dataset.targetResourceId || "",
          source: datasourceRow.dataset.source || "",
          type: datasourceRow.dataset.type || "",
          target: datasourceRow.dataset.target || "",
          connectivityStatus: datasourceRow.dataset.connectivityStatus || "unknown",
          lastSyncedAt: nowText(),
        });
        setDatasourceRowEditing(datasourceRow, false);
        updateConfigStats(datasourceRow.closest(".table-card")?.querySelector("[data-config-stats='datasource-mapping']"));
        showToast(payload.message || (enabled ? "数据源映射已停用" : "数据源映射已启用"));
        scrollToSection(getSectionAnchor(datasourceRow, "section-datasource-mappings"));
      } catch (error) {
        showToast(error.message || "数据源映射状态更新失败");
      } finally {
        button.disabled = false;
      }
      return;
    }
    if (button.dataset.action === "save-all-datasource") {
      let savedCount = 0;
      for (const row of document.querySelectorAll("[data-datasource-row]")) {
        const targetValue = row.querySelector("[data-datasource-target]")?.value || "";
        const sourceResourceId = row.dataset.sourceResourceId || row.querySelector("[data-datasource-source]")?.value || "";
        if (!sourceResourceId || !targetValue) continue;
        const payload = await postForm("/config/datasource-mappings/save", {
          source_resource_id: sourceResourceId,
          target_resource_id: targetValue,
        });
        if (payload.status !== "success") {
          showToast(payload.message || "批量保存时出现失败项");
          continue;
        }
        applyDatasourceRowState(row, {
          sourceResourceId,
          targetResourceId: targetValue,
          source: datasourceSourceMeta[sourceResourceId]?.name || row.dataset.source || "",
          type: row.dataset.type || datasourceSourceMeta[sourceResourceId]?.type || "",
          target: datasourceOptionMeta[targetValue]?.name || row.dataset.target || "",
          connectivityStatus: datasourceSourceMeta[sourceResourceId]?.connectivityStatus || "unknown",
          lastSyncedAt: nowText(),
        });
        row.dataset.mappingEnabled = "1";
        savedCount += 1;
      }
      renderTable(document.querySelector(".config-datasource-table")?.closest(".table-card"));
      showToast(`已保存 ${savedCount} 条数据源映射`);
    }
  });

  const activateStatCard = (content, status) => {
    content.querySelectorAll("[data-stat-card]").forEach((card) => {
      card.classList.toggle("active", card.dataset.statFilter === status);
    });
  };

  document.querySelectorAll(".tabs a").forEach((tab) => {
    tab.addEventListener("click", (event) => {
      const filterValue = tab.dataset.tabFilter;
      if (filterValue === undefined) return;
      event.preventDefault();
      tab.parentElement.querySelectorAll("a").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      const scope = tab.closest(".content").querySelector("[data-filter-scope]");
      const moduleFilter = scope?.querySelector("[data-filter='module']");
      const statusFilter = scope?.querySelector("[data-filter='status']");
      if (moduleFilter) moduleFilter.value = filterValue;
      if (!moduleFilter && statusFilter) statusFilter.value = filterValue;
      if (moduleFilter && statusFilter) {
        statusFilter.value = "";
        activateStatCard(tab.closest(".content"), "");
      }
      if (scope) applyFilter(scope);
    });
  });

  document.querySelectorAll("[data-git-binding-row]").forEach((row) => {
    row.dataset.initialRepoUrl = row.querySelector("input[name='repo_url']")?.value || "";
    row.dataset.initialBranch = row.querySelector("[data-git-branch-select]")?.value || "";
    const form = row.querySelector("form[id^='git-binding-form-']");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const projectSpaceId = form.querySelector("input[name='project_space_id']")?.value || "";
      const moduleType = form.querySelector("input[name='module_type']")?.value || "";
      const repoUrl = row.querySelector("input[name='repo_url']")?.value?.trim() || "";
      const branch = row.querySelector("[data-git-branch-select]")?.value || "";
      if (!projectSpaceId || !branch) {
        showToast("请先填写仓库地址并选择跟踪分支");
        return;
      }
      const submitButton = form.querySelector("button[type='submit']");
      submitButton?.toggleAttribute("disabled", true);
      try {
        const payload = await postForm("/config/git-repos/save", {
          project_space_id: projectSpaceId,
          module_type: moduleType,
          repo_url: repoUrl,
          branch,
        });
        if (!payload.status || payload.status !== "success") {
          throw new Error(payload.message || "Git 绑定保存失败");
        }
        row.dataset.initialRepoUrl = repoUrl;
        row.dataset.initialBranch = branch;
        const branchSelect = row.querySelector("[data-git-branch-select]");
        if (branchSelect) branchSelect.dataset.currentBranch = branch;
        setGitBindingRowEditing(row, false);
        showToast(payload.message || "Git 绑定已保存");
        scrollToSection(getSectionAnchor(row, "section-git-bindings"));
      } catch (error) {
        showToast(error.message || "Git 绑定保存失败");
      } finally {
        submitButton?.toggleAttribute("disabled", false);
      }
    });
  });

  document.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      button.classList.add("pulse");
      window.setTimeout(() => button.classList.remove("pulse"), 260);
    });
  });

  document.querySelectorAll("[data-action='filter-table']").forEach((button) => {
    button.addEventListener("click", () => {
      const scope = button.closest("[data-filter-scope]");
      if (scope) applyFilter(scope);
    });
  });

  document.querySelectorAll("[data-action='reset-filter']").forEach((button) => {
    button.addEventListener("click", () => {
      const scope = button.closest("[data-filter-scope]");
      if (!scope) return;
      scope.querySelectorAll("input").forEach((input) => { input.value = ""; });
      scope.querySelectorAll("select").forEach((select) => { select.selectedIndex = 0; });
      const content = scope.closest(".content");
      content.querySelectorAll(".tabs a").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.tabFilter === "");
      });
      activateStatCard(content, "");
      applyFilter(scope);
    });
  });

  document.querySelectorAll("[data-stat-card]").forEach((card) => {
    card.addEventListener("click", () => {
      const content = card.closest(".content");
      const scope = content.querySelector("[data-filter-scope]");
      const statusFilter = scope?.querySelector("[data-filter='status']");
      if (!statusFilter) return;
      statusFilter.value = card.dataset.statFilter || "";
      activateStatCard(content, statusFilter.value);
      applyFilter(scope);
    });
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        card.click();
      }
    });
  });

  const selectedTaskCheckboxes = document.querySelectorAll(".select-task-checkbox");
  const updateSelectedTaskCount = () => {
    const count = document.querySelectorAll(".select-task-checkbox:checked").length;
    document.querySelectorAll("[data-selected-count]").forEach((item) => {
      item.textContent = String(count);
    });
  };

  selectedTaskCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", updateSelectedTaskCount);
  });

  document.querySelectorAll("[data-action='confirm-selected']").forEach((button) => {
    button.addEventListener("click", () => {
      const ids = Array.from(document.querySelectorAll(".select-task-checkbox:checked"))
        .map((checkbox) => checkbox.value);
      if (!ids.length) {
        showToast("请至少选择 1 个任务");
        return;
      }
      const projectSpaceId = button.dataset.projectSpaceId || "";
      const draftId = button.dataset.draftId || "";
      const projectQuery = projectSpaceId ? `&project_space_id=${encodeURIComponent(projectSpaceId)}` : "";
      const draftQuery = draftId ? `&draft_id=${encodeURIComponent(draftId)}` : "";
      window.location.href = `/confirm?step=2${projectQuery}${draftQuery}&task_ids=${encodeURIComponent(ids.join(","))}`;
    });
  });

  document.querySelectorAll("[data-href]").forEach((item) => {
    item.addEventListener("click", () => {
      window.location.href = item.dataset.href;
    });
  });

  document.querySelector(".project-select")?.addEventListener("click", () => {
    showToast("切换项目空间后，任务列表会按对应 Git 仓库重新计算");
  });

  document.querySelectorAll("[data-filter-scope]").forEach((scope) => {
    const hasInitialValue = Array.from(scope.querySelectorAll("input, select")).some((item) => item.value);
    applyFilter(scope, { silent: true });
    if (hasInitialValue) showToast("已按入口条件筛选列表");
  });
});
