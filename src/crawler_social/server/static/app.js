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
    var log = document.querySelector("[data-crawl-log]");
    var dropped = document.querySelector("[data-crawl-dropped]");
    var droppedCount = document.querySelector("[data-crawl-dropped-count]");
    var failures = 0;

    // Only scroll the log for a reader who is already at the bottom, so
    // polling never yanks the view away from a line being read.
    var updateLog = function (lines) {
      if (!log || !lines) return;
      var text = lines.join("\n");
      if (text === log.textContent) return;
      var atBottom =
        log.scrollHeight - log.scrollTop - log.clientHeight < 24;
      log.textContent = text;
      if (atBottom) log.scrollTop = log.scrollHeight;
    };

    var refresh = function () {
      fetch("/api/crawl/status", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) throw new Error("bad status response");
          failures = 0;
          updateLog(data.lines);
          if (dropped && droppedCount) {
            droppedCount.textContent = data.lines_dropped;
            dropped.hidden = !data.lines_dropped;
          }
          if (!data.running && data.exit_code !== null) {
            // Crawl finished: reload so the form and its summary return.
            statusPanel.textContent =
              "— finished with exit code " + data.exit_code + ", reloading…";
            setTimeout(function () { window.location.reload(); }, 1500);
            return;
          }
          // elapsed_seconds is null until the job has a start time; without
          // the guard this rendered "for NaNs".
          var elapsed = data.elapsed_seconds;
          statusPanel.textContent = data.running
            ? "for " + (elapsed === null ? 0 : Math.round(elapsed)) + "s…"
            : "— idle";
          if (data.stop_requested) {
            statusPanel.textContent += " (stopping)";
          }
          setTimeout(refresh, 2000);
        })
        .catch(function () {
          // Back off instead of hammering a server that is down, and give
          // up rather than polling a dead endpoint forever.
          failures += 1;
          if (failures > 5) {
            statusPanel.textContent = "— status unavailable; reload the page";
            return;
          }
          setTimeout(refresh, 2000 * failures);
        });
    };
    refresh();
  }
})();
