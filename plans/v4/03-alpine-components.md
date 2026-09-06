# 03 — Alpine components

## Outcome

`Alpine.data()` components in `app.js` replace the last imperative block
in that file, and give client-side state a shape it can grow into. One
component is a like-for-like replacement of existing code; two are new
behaviour and marked as such, so the step can be taken in part.

## Depends on

- [Step 01](./01-vendor-and-config.md) — the CSP build must be vendored
  and verified, or nothing here runs.
- [Step 02](./02-htmx-interactions.md) — `app.js` is already trimmed to
  the `js` class and the log scroll anchor.

## Read this before writing a component

The `@alpinejs/csp` build is not stock Alpine with a flag. It is a
different authoring grammar, because `script-src 'self'` forbids
`unsafe-eval` and stock Alpine compiles every attribute value with the
`Function` constructor. Under the CSP build:

| Allowed | Not allowed |
| --- | --- |
| `x-data="copyButton"` (names a registered component) | `x-data="{ open: false }"` |
| `x-on:click="copy"` (names a method) | `x-on:click="open = !open"` |
| `x-on:click="panel.close"` (a dotted path) | `x-on:click="close()"` |
| `x-text="label"` (names a property) | `x-text="'Copied ' + n"` |
| `x-show="copied"` | `x-show="!copied"` |
| `x-bind:disabled="busy"` | `x-bind:disabled="busy && !done"` |

Dotted paths work because the build resolves an attribute by
`value.split(".").reduce(...)` over the component's scope. Anything else
with a computation in it moves into the `Alpine.data()` registration as a
method or a getter. There is no escape hatch.

The failure mode is worth knowing precisely, because it is not what you
would guess. Given an expression it cannot interpret, the CSP build
evaluates nothing — so there is **no CSP violation**. It logs
`Alpine Error: Alpine is unable to interpret the following expression
using the CSP-friendly build: "<the expression>"` as a `console.warn` and
the attribute silently does nothing. A CSP violation instead means the
*stock* build was vendored. Step 04 greps the templates for the
right-hand column because neither failure surfaces in a test run.

Two mechanical consequences:

- **Registrations go in `app.js`, inside an `alpine:init` listener.**
  Alpine dispatches that event when it starts; `app.js` is deferred ahead
  of `alpine-csp.min.js`, so the listener is always in place first.
- **The server-rendered markup must be correct with JavaScript off.**
  Alpine only ever *changes* what Jinja rendered. Any element that exists
  only to show an Alpine state gets `x-cloak`, and `app.css` needs the
  matching rule once:

  ```css
  /* Alpine removes x-cloak on init; without JS these never appear. */
  [x-cloak] { display: none !important; }
  ```

## Work, in order

### 1. Required — the clipboard button on `/posts/{id}`

This replaces the `[data-copy-target]` block in `app.js`. Today that block
sweeps the document by attribute selector, mutates
`button.textContent` directly, and restores it on a bare `setTimeout`
with no way to express "busy".

**`post.html`** — the button currently reads:

```html
<button class="button" type="button" data-copy-target="#post-text">Copy text</button>
```

Becomes:

```html
<button class="button" type="button"
        x-data="copyButton" x-on:click="copy"
        x-bind:disabled="copied"
        data-copy-target="#post-text">
  <span x-show="idle">Copy text</span>
  <span x-show="copied" x-cloak>Copied</span>
</button>
```

`idle` and `copied` are plain booleans, so both `x-show` attributes are
bare property names. `x-cloak` on the second span is what keeps the button
reading "Copy text" and nothing else when JavaScript is off. `x-show`
toggles `element.style.display` through the CSSOM, which CSP does not
police — only `<style>` elements and `style=` attributes in parsed markup
are subject to `style-src`. (This is also why Step 04's `inline style`
grep is over template *sources* and not over rendered output: a
`style` attribute Alpine sets at runtime is legitimate, one Jinja
renders is not.)

**`app.js`**:

```js
document.addEventListener("alpine:init", function () {
  Alpine.data("copyButton", function () {
    return {
      idle: true,
      copied: false,
      copy: function () {
        var target = this.$el.getAttribute("data-copy-target");
        var source = target && document.querySelector(target);
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
          function () {}
        );
      },
    };
  });
});
```

The `data-copy-target` attribute stays: it is how the template says which
element to read, and moving it into the component would hard-code a
selector in JavaScript. The silent `catch` is carried over unchanged — a
denied clipboard permission should not throw into the console.

### 2. Optional — full error text on `/runs`

**This is new behaviour.** `runs.html` renders `run.error | excerpt(120)`,
so a longer error is unreadable in the list and the only way to see it is
to open `/runs/{id}`. A disclosure fixes that in place.

**`runs.html`** — the error cell becomes:

```html
<td class="col-error" {% if run.error and run.error | length > 120 %}x-data="disclosure"{% endif %}>
  {% if run.error and run.error | length > 120 %}
  <span x-show="closed">{{ run.error | excerpt(120) }}</span>
  <span x-show="open" x-cloak>{{ run.error }}</span>
  <button type="button" class="button" x-on:click="toggle" x-text="label" x-cloak></button>
  {% else %}
  {{ run.error | excerpt(120) }}
  {% endif %}
</td>
```

**`app.js`**:

```js
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
```

`this.open = !this.open` is fine — the restriction is on expressions in
*attributes*, not in the registration. The button carries `x-cloak` so no
dead control appears without JavaScript, and the truncated text stays
visible in that case, exactly as today.

One thing to weigh before taking this: it renders the full error text for
every long-errored run on the page. A run that failed with a stack trace
makes the list page large. If that shows up, cap it —
`{{ run.error | excerpt(2000) }}` in the expanded span — rather than
dropping the component.

### 3. Optional — the permalink-cost estimate on `/crawl`

**This is new behaviour**, and it is the one with the best argument behind
it. `plans/v3` is explicit that per-post permalink visits are the most
bot-visible thing this project does, and the crawl form currently explains
that in static prose: *"0 collects none. Each post asked for costs one
extra page visit."* The form has both numbers on it, so it can say what
the run will actually cost.

**`crawl.html`** — put `x-data` on the **form**, not on a wrapper around
the two number inputs. `.filter-bar` is a flex container whose `<label>`s
are its flex items, so a wrapper would collapse two items into one and
change the layout — and v4 restyles nothing.

```html
<form class="filter-bar" method="post" action="/crawl"
      x-data="crawlCost" x-on:input="recalc">
  …
  <label>
    Top comments per post
    <input type="number" name="comments" value="{{ comments_value }}" min="0" max="100">
    <span class="muted">0 collects none. Each post asked for costs one extra page visit.</span>
    <span class="muted" x-text="estimate" x-cloak></span>
  </label>
```

Note what is **not** there: `x-model`. Binding the inputs would require
Alpine to write back into the component, and that direction is not
something this build documents. `x-on:input="recalc"` on the form names a
method, which is unambiguously supported, and the method reads the two
inputs out of `this.$el`. One less thing resting on an assumption.

**`app.js`** — and one trap to know about first. `app.js` is a static
file, not a template: `StaticFiles` serves it and Jinja never sees it, so
a server value cannot be interpolated into it, and templating it would
mean an inline `<script>` that the CSP forbids. Read the initial values
out of the DOM:

```js
Alpine.data("crawlCost", function () {
  return {
    estimate: "",
    init: function () { this.recalc(); },
    recalc: function () {
      var limit = Number(this._value("limit")) || 0;
      var comments = Number(this._value("comments")) || 0;
      if (!comments || !limit) { this.estimate = ""; return; }
      this.estimate = "≈ " + limit + " extra permalink visit" +
                      (limit === 1 ? "" : "s") + " for comments";
    },
    _value: function (name) {
      var input = this.$el.querySelector("[name='" + name + "']");
      return input ? input.value : "";
    },
  };
});
```

`estimate` is a plain property set by a method rather than a getter — the
attribute then only has to name a property, which is the least this build
has to interpret. Note that the real ceiling is
`config.comments_max_posts`, which the form does not carry; if the
estimate should respect it, add it to the form as a `data-` attribute
rather than reaching for the config from JavaScript.

### 4. Order the registrations

All three go inside the single `alpine:init` listener in `app.js`, above
the `htmx:beforeSwap` / `htmx:afterSwap` listeners from Step 02. Keep the
file's existing header comment accurate: it currently claims *"No
framework, no build step, no network requests."* Two of those three are
still true and the first is not — rewrite it to say what is now true, that
Alpine and htmx are vendored files and every feature still works with the
file disabled.

## Required tests

- **The copy button renders its label without JavaScript.** `GET
  /posts/p1` contains `Copy text` inside the button, and the `Copied` span
  carries `x-cloak`. This is the whole no-JS guarantee for §1.
- **No stock-Alpine expressions in any template.** Part of the Step 04
  grep test; listed here because §1–§3 are what it is guarding.
- **`x-data` values name registered components.** Collect every `x-data`
  value in `templates/` and assert each appears as an `Alpine.data("…")`
  registration in `app.js`. A typo is otherwise silent.
- **`[x-cloak]` has a CSS rule.** Assert `app.css` contains the
  `[x-cloak]` selector. Without it every optional component leaks a dead
  control onto the page for no-JS readers, and nothing else would catch
  it.
- **`test_no_inline_script_or_style_on_any_page` still passes**, including
  on `/posts/p1` and `/runs` with a long-errored run seeded.
- If §2 is taken: a run with a 500-character error renders both the
  truncated and the full text, and a run with a short error renders
  neither an `x-data` nor a button.

## Verification

```console
$ uv run pytest -q
$ uv run crawler serve
```

- `/posts/{id}` — the button copies, reads "Copied" for 1.2 s, is disabled
  while it does, and the console stays clean. A clean console is the real
  assertion here, and the two ways it can be dirty mean different things:
  `Refused to evaluate a string as JavaScript` means the **stock** Alpine
  build was vendored, and `Alpine Error: … CSP-friendly build` means the
  right build is loaded but an attribute contains an expression. Both
  appear on first interaction, not on load.
- `/runs` with a long error — expands and collapses.
- `/crawl` — typing in either number updates the estimate; setting
  comments to 0 makes it disappear.
- With JavaScript disabled: the copy button shows one label and does
  nothing when clicked, the runs error stays truncated with no button
  visible, and the crawl form shows only its static prose.

## If you skip this step

Steps 01, 02 and 04 stand without it, and they carry the entire
performance win. What you keep by skipping: the ~16-line clipboard block
in `app.js`, 45 KB of vendored JavaScript, and about 45 lines of component
code for the two optional features. What you give up: the two
optional components, and the structure that stops `app.js` turning back
into a pile of `querySelectorAll` loops the next time the viewer grows.
That trade is a judgement call about where this tool is going, not a
correctness question — which is why Alpine is its own step and not folded
into Step 02.
