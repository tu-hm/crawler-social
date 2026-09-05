/*
 * Progressive enhancements only: every viewer feature must work with this
 * file disabled. No framework, no build step, no network requests.
 */
(function () {
  "use strict";

  document.documentElement.classList.add("js");

  // Filter forms: submit on change so adjusting a select or date applies
  // immediately. Text fields debounce-submit after a pause in typing.
  document.querySelectorAll("form[data-auto]").forEach(function (form) {
    form.querySelectorAll("select, input[type='date']").forEach(function (el) {
      el.addEventListener("change", function () {
        form.submit();
      });
    });
    var timer = null;
    form.querySelectorAll("input[type='search']").forEach(function (el) {
      el.addEventListener("input", function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
          form.submit();
        }, 500);
      });
    });
  });

  // Copy-to-clipboard buttons (post detail text).
  document.querySelectorAll("[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var source = document.querySelector(button.getAttribute("data-copy-target"));
      if (!source || !navigator.clipboard) return;
      navigator.clipboard.writeText(source.textContent || "").then(
        function () {
          var original = button.textContent;
          button.textContent = "Copied";
          setTimeout(function () {
            button.textContent = original;
          }, 1200);
        },
        function () {}
      );
    });
  });

  // Crawl status polling (only when the status panel is present).
  var statusPanel = document.querySelector("[data-crawl-status]");
  if (statusPanel) {
    var refresh = function () {
      fetch("/api/crawl/status")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          if (!data.running && data.exit_code !== null) {
            // Crawl finished: reload so the form and its summary return.
            setTimeout(function () { window.location.reload(); }, 1500);
            statusPanel.textContent =
              "Finished with exit code " + data.exit_code + " — reloading…";
            return;
          }
          statusPanel.textContent = data.running
            ? "Crawl running for " + Math.round(data.elapsed_seconds) + "s"
            : "Idle";
          setTimeout(refresh, 2000);
        })
        .catch(function () {
          setTimeout(refresh, 2000);
        });
    };
    refresh();
  }
})();
