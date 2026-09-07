// Alpine is the CSP build: no unsafe-eval under script-src 'self', so an attribute
// may only name a property or method registered below.
(function () {
  "use strict";

  document.documentElement.classList.add("js");

  // Follow the log tail only for a reader already at the bottom; htmx
  // replaces this element's contents on every poll.
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

  document.addEventListener("alpine:init", function () {
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

    // Reads the inputs on `input`: x-model's write-back into the component
    // is not a path this build's docs promise.
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
