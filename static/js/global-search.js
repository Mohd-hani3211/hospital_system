(function () {
  var MIN_QUERY_LENGTH = 2;
  var DEBOUNCE_DELAY = 280;
  var stateByRoot = new WeakMap();

  function getRoot(element) {
    return element ? element.closest('[data-global-search]') : null;
  }

  function getState(root) {
    var state = stateByRoot.get(root);
    if (!state) {
      state = { timer: null, controller: null };
      stateByRoot.set(root, state);
    }
    return state;
  }

  function getInput(root) {
    return root.querySelector('[data-global-search-input]');
  }

  function getPanel(root) {
    return root.querySelector('[data-global-search-results]');
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function hidePanel(root) {
    var panel = getPanel(root);
    if (!panel) {
      return;
    }
    panel.hidden = true;
    panel.innerHTML = '';
  }

  function closeMobileSearchPanels() {
    document.querySelectorAll('[data-mobile-search-toggle]').forEach(function (toggle) {
      var panelId = toggle.getAttribute('aria-controls');
      var panel = panelId ? document.getElementById(panelId) : null;
      if (panel) {
        panel.hidden = true;
      }
      toggle.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMobileSearch(toggle) {
    var panelId = toggle.getAttribute('aria-controls');
    var panel = panelId ? document.getElementById(panelId) : null;
    if (!panel) {
      return;
    }

    var shouldOpen = panel.hidden;
    closeMobileSearchPanels();
    panel.hidden = !shouldOpen;
    toggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');

    if (shouldOpen) {
      var input = panel.querySelector('[data-global-search-input]');
      if (input) {
        window.setTimeout(function () {
          input.focus();
        }, 30);
      }
    }
  }

  function showMessage(root, message, className) {
    var panel = getPanel(root);
    if (!panel) {
      return;
    }
    panel.innerHTML = '<div class="global-search-message ' + (className || '') + '">' + escapeHtml(message) + '</div>';
    panel.hidden = false;
  }

  function renderResults(root, results) {
    var panel = getPanel(root);
    if (!panel) {
      return;
    }

    if (!results || !results.length) {
      showMessage(root, 'لا توجد نتائج', 'is-empty');
      return;
    }

    panel.innerHTML = results.map(function (result) {
      return (
        '<a class="global-search-result" href="' + escapeHtml(result.url) + '">' +
          '<span class="global-search-result-type">' + escapeHtml(result.type) + '</span>' +
          '<span class="global-search-result-title">' + escapeHtml(result.title) + '</span>' +
          (result.description ? '<span class="global-search-result-description">' + escapeHtml(result.description) + '</span>' : '') +
        '</a>'
      );
    }).join('');
    panel.hidden = false;
  }

  function abortPrevious(state) {
    if (state.controller) {
      state.controller.abort();
      state.controller = null;
    }
  }

  function runSearch(root) {
    var input = getInput(root);
    var url = root.getAttribute('data-search-url');
    var state = getState(root);
    var query = input ? input.value.trim() : '';

    abortPrevious(state);

    if (!url || query.length < MIN_QUERY_LENGTH) {
      hidePanel(root);
      return;
    }

    showMessage(root, 'جاري البحث...', 'is-loading');

    var requestedQuery = query;
    var controller = new AbortController();
    state.controller = controller;
    var requestUrl = new URL(url, window.location.origin);
    requestUrl.searchParams.set('q', query);

    fetch(requestUrl.toString(), {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      signal: controller.signal
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('Search request failed');
        }
        return response.json();
      })
      .then(function (data) {
        var currentInput = getInput(root);
        var currentQuery = currentInput ? currentInput.value.trim() : '';
        if (currentQuery !== requestedQuery) {
          return;
        }
        renderResults(root, data.results || []);
      })
      .catch(function (error) {
        if (error.name === 'AbortError') {
          return;
        }
        showMessage(root, 'تعذر تنفيذ البحث الآن', 'is-error');
      })
      .finally(function () {
        if (state.controller === controller) {
          state.controller = null;
        }
      });
  }

  function scheduleSearch(root) {
    var state = getState(root);
    if (state.timer) {
      window.clearTimeout(state.timer);
    }
    state.timer = window.setTimeout(function () {
      runSearch(root);
    }, DEBOUNCE_DELAY);
  }

  document.addEventListener('input', function (event) {
    if (!event.target.matches('[data-global-search-input]')) {
      return;
    }
    var root = getRoot(event.target);
    if (root) {
      scheduleSearch(root);
    }
  });

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }

    var button = target.closest('[data-global-search-button]');
    if (button) {
      var buttonRoot = getRoot(button);
      if (!buttonRoot) {
        return;
      }
      var input = getInput(buttonRoot);
      if (input) {
        input.focus();
      }
      runSearch(buttonRoot);
      return;
    }

    var mobileToggle = target.closest('[data-mobile-search-toggle]');
    if (mobileToggle) {
      event.preventDefault();
      toggleMobileSearch(mobileToggle);
      return;
    }

    if (!target.closest('[data-global-search]')) {
      document.querySelectorAll('[data-global-search]').forEach(hidePanel);
      if (!target.closest('.hospital-mobile-search-panel')) {
        closeMobileSearchPanels();
      }
    }
  });

  document.addEventListener('submit', function (event) {
    var root = getRoot(event.target);
    if (root) {
      event.preventDefault();
      runSearch(root);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') {
      return;
    }
    document.querySelectorAll('[data-global-search]').forEach(hidePanel);
    closeMobileSearchPanels();
  });
}());
