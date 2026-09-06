/*
 * Progressive enhancements only: every viewer feature must work with this
 * file, and both vendored libraries, disabled. No build step and no network
 * requests -- htmx and Alpine are plain files under /static/vendor.
 *
 * What used to live here and no longer does: the filter-form auto-submit
 * (now hx-trigger on the forms themselves) and the /crawl status poll (now
 * hx-trigger="every 2s" on a panel that swaps itself away when the crawl
 * ends). See plans/v4/02-htmx-interactions.md.
 *
 * Alpine is the @alpinejs/csp build, because script-src 'self' carries no
 * unsafe-eval. Attributes may only NAME a property or method -- every
 * expression lives in the Alpine.data() registrations below.
 */
(function () {
  "use strict";

  document.documentElement.classList.add("js");

  // --- The crawl log's scroll anchor ---------------------------------------
  //
  // The log's innerHTML is swapped out of band on every /crawl poll. Only
  // follow the tail for a reader who is already at the bottom, so polling
  // never pulls the view away from a line being read. This is an htmx event
  // listener rather than an Alpine component on purpose: Alpine state lives
  // on an element, and this element's contents are replaced every 2s.
  var logAtBottom = true;

  document.addEventListener("htmx:beforeSwap", function () {
    var log = document.getElementById("crawl-output");
    if (!log) return;
    logAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
  });

  document.addEventListener("htmx:afterSwap", function () {
    var log = document.getElementById("crawl-output");
    if (log && logAtBottom) log.scrollTop = log.scrollHeight;
  });

  // --- Alpine components ---------------------------------------------------
  document.addEventListener("alpine:init", function () {
    // Copy the post text. Replaces the old [data-copy-target] loop; the
    // attribute stays because it is the template's way of saying which
    // element to read, and a selector hard-coded here would be worse.
    Alpine.data("copyButton", function () {
      return {
        idle: true,
        copied: false,
        copy: function () {
          var selector = this.$el.getAttribute("data-copy-target");
          var source = selector && document.querySelector(selector);
          if (!source || !navigator.clipboard) return;
          var self = this;
          navigator.clipboard.writeText(source.textContent || "").then(
            function () {
              self.idle = false;
              self.copied = true;
              setTimeout(function () {
                self.idle = true;
                self.copied = false;
              }, 1200);
            },
            // A denied clipboard permission should not reach the console.
            function () {}
          );
        },
      };
    });

    // Show a run's full error text in the list, instead of only the first
    // 120 characters with /runs/{id} as the sole way to read the rest.
    Alpine.data("disclosure", function () {
      return {
        closed: true,
        open: false,
        label: "more",
        toggle: function () {
          this.open = !this.open;
          this.closed = !this.open;
          this.label = this.open ? "less" : "more";
        },
      };
    });

    // What the crawl form's numbers actually cost. Every comment asked for
    // is one permalink navigation, which plans/v3 calls the most bot-visible
    // thing this project does, so the form says so in numbers.
    //
    // Reads the inputs on `input` rather than binding them with x-model:
    // x-model has to write back into the component, and that path is not
    // one this build's docs promise. A method call is unambiguous.
    Alpine.data("crawlCost", function () {
      return {
        estimate: "",
        init: function () {
          this.recalc();
        },
        recalc: function () {
          var limit = Number(this._value("limit")) || 0;
          var comments = Number(this._value("comments")) || 0;
          if (!comments || !limit) {
            this.estimate = "";
            return;
          }
          this.estimate =
            "≈ " + limit + " extra permalink visit" +
            (limit === 1 ? "" : "s") + " for comments";
        },
        _value: function (name) {
          var input = this.$el.querySelector("[name='" + name + "']");
          return input ? input.value : "";
        },
      };
    });
  });
})();
