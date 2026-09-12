"""DOM-derived privacy rectangles; this is rule-based masking, not computer vision.

The JavaScript must run in the driver's isolated world at the same validated
snapshot as the viewport screenshot. It returns geometry only, never matched text.
"""

from __future__ import annotations

import math

PRIVACY_REGIONS_JS = r"""function(knownSecrets) {
  const result = {
    url: location.href,
    coordinate_space: 'viewport-css',
    viewport: {width: innerWidth, height: innerHeight},
    visual_scale: visualViewport?.scale || 1,
    visual_offset_x: visualViewport?.offsetLeft || 0,
    visual_offset_y: visualViewport?.offsetTop || 0,
    regions: [], warnings: [], complete: true,
    detector: 'DOM fields, known values and text patterns; no computer vision'
  };
  const deadline = performance.now() + 1500;
  const MAX_ELEMENTS = 12000, MAX_TEXT = 250000, MAX_REGIONS = 4000;
  const fail = message => { result.complete = false; if (!result.warnings.includes(message)) result.warnings.push(message); };
  const bounded = () => {
    if (performance.now() > deadline) { fail('DOM privacy collection timed out.'); return false; }
    return true;
  };
  const visible = e => {
    const style = getComputedStyle(e), rect = e.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse'
      && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
      && rect.top < innerHeight && rect.left < innerWidth;
  };
  const addRect = (rect, reason) => {
    if (![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)) { fail('Invalid privacy geometry.'); return; }
    if (rect.width <= 0 || rect.height <= 0 || rect.right <= 0 || rect.bottom <= 0
        || rect.left >= innerWidth || rect.top >= innerHeight) return;
    if (result.regions.length >= MAX_REGIONS) { fail('Too many privacy regions.'); return; }
    result.regions.push({x: rect.x, y: rect.y, width: rect.width, height: rect.height, reason});
  };
  const esc = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  let secrets = Array.isArray(knownSecrets) ? knownSecrets.filter(x => typeof x === 'string' && x.trim()) : [];
  if (!Array.isArray(knownSecrets)) fail('Known-value input was not available.');
  const roots = [document], all = [];
  try {
    // MutationObserver cannot detect transforms driven by CSS animations or
    // font reflow. Block these unstable layouts instead of trusting stale boxes.
    if (document.fonts && document.fonts.status !== 'loaded') fail('Wait for page fonts to finish loading.');
    if (document.getAnimations().some(animation => animation.playState === 'running' || animation.pending)) {
      fail('Pause active page animations before capturing a screenshot.');
    }
    for (let r = 0; r < roots.length && result.complete; r++) {
      for (const e of roots[r].querySelectorAll('*')) {
        if (all.length >= MAX_ELEMENTS || !bounded()) { fail('DOM privacy collection exceeded its limit.'); break; }
        all.push(e);
        if (e.shadowRoot) roots.push(e.shadowRoot);
      }
    }
    for (const e of all) {
      if (!bounded()) break;
      if (!visible(e)) continue;
      const tag = e.tagName.toLowerCase(), type = String(e.type || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag) && !['submit', 'button', 'reset', 'image'].includes(type)) {
        const value = type === 'password' ? '' : String(e.value || '');
        if (type === 'password' || value.trim() || e.autocomplete === 'one-time-code') addRect(e.getBoundingClientRect(), type === 'password' ? 'password_field' : 'populated_field');
        if (value.trim() && !['checkbox', 'radio'].includes(type)) secrets.push(value);
      }
      if (['img', 'video', 'canvas', 'iframe', 'frame', 'svg', 'object', 'embed'].includes(tag) || (tag === 'input' && type === 'image')) {
        addRect(e.getBoundingClientRect(), ['iframe', 'frame'].includes(tag) ? 'embedded_frame' : 'uninspected_media');
      }
      if (tag.includes('-') && !e.shadowRoot) addRect(e.getBoundingClientRect(), 'uninspectable_component');
      const style = getComputedStyle(e);
      if ([style.backgroundImage, style.borderImageSource, style.listStyleImage].some(x => /url\(/i.test(x || ''))) addRect(e.getBoundingClientRect(), 'background_media');
    }
    secrets = [...new Set(secrets)].sort((a, b) => b.length - a.length);
    if (secrets.length > 1000 || secrets.reduce((sum, x) => sum + x.length, 0) > 200000) fail('Known-value matching exceeded its limit.');
    const expressions = [];
    if (result.complete && secrets.length) {
      expressions.push({reason: 'known_value', regex: new RegExp(secrets.map(secret => {
        const body = esc(secret.normalize('NFKC')).replace(/\s+/g, '\\s*');
        return secret.length < 3 ? '(?<!\\w)' + body + '(?!\\w)' : body;
      }).join('|'), 'giu')});
    }
    expressions.push(
      {reason: 'email_pattern', regex: /[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+/giu},
      {reason: 'identifier_pattern', regex: /\b[A-Z]{5}\d{4}[A-Z]\b/giu},
      {reason: 'number_pattern', regex: /(?<!\w)(?:\+?\d[\s().-]*){8,19}(?!\w)/gu},
      {reason: 'date_pattern', regex: /\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b/gu},
      {reason: 'amount_pattern', regex: /(?:₹|\$|€|£|\b(?:INR|USD|EUR|GBP|Rs\.?))\s*[+-]?\d[\d,]*(?:\.\d+)?/giu},
      {reason: 'labeled_private_text', regex: /\b(?:full\s+name|name|email(?:\s+address)?|phone(?:\s+number)?|mobile|address|date\s+of\s+birth|dob|password|passcode|otp|pan|aadhaar|aadhar|account\s+number|bank\s+account|ifsc)\s*[:=]\s*([^\n\r;|]{1,160})/giu, valueGroup: true}
    );
    const nodes = [];
    let joined = '', previousBlock = null;
    const textBlock = element => {
      for (let e = element; e; e = e.parentElement) {
        if (/^(block|flow-root|flex|grid|table|list-item)/.test(getComputedStyle(e).display)) return e;
      }
      return element.getRootNode();
    };
    for (const root of roots) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!bounded()) break;
        const parent = node.parentElement;
        if (!parent || !node.data.trim() || parent.closest('script,style,noscript,template,textarea,select,option')) continue;
        const visibility = getComputedStyle(parent).visibility;
        if (visibility === 'hidden' || visibility === 'collapse') continue;
        const probe = document.createRange(); probe.selectNodeContents(node);
        const rect = probe.getBoundingClientRect(); probe.detach();
        if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) continue;
        const text = node.data.normalize('NFKC');
        if (joined.length + text.length > MAX_TEXT) { fail('Visible text exceeded the privacy limit.'); break; }
        // Preserve block boundaries without breaking values split across inline
        // spans. Otherwise an email immediately followed by a PAN in the next
        // paragraph could erase the identifier's required word boundary.
        const block = textBlock(parent);
        if (previousBlock !== null && previousBlock !== block) joined += '\n';
        previousBlock = block;
        nodes.push({node, start: joined.length, end: joined.length + text.length, normalized: text !== node.data});
        joined += text;
      }
    }
    const markText = (start, end, reason) => {
      for (const item of nodes) {
        if (item.end <= start) continue;
        if (item.start >= end) break;
        const range = document.createRange();
        const begin = item.normalized ? 0 : Math.max(0, start - item.start);
        const finish = item.normalized ? item.node.length : Math.min(item.node.length, end - item.start);
        if (finish <= begin) continue;
        range.setStart(item.node, begin); range.setEnd(item.node, finish);
        for (const rect of range.getClientRects()) addRect(rect, reason);
        range.detach();
      }
    };
    for (const expression of expressions) {
      let match;
      while ((match = expression.regex.exec(joined))) {
        if (!bounded()) break;
        const value = expression.valueGroup ? match[1] : match[0];
        const begin = expression.valueGroup ? match.index + match[0].length - value.length : match.index;
        markText(begin, begin + value.length, expression.reason);
        if (!match[0].length) expression.regex.lastIndex++;
      }
    }
    // Sensitive generated text has no reliable DOM Range. Block instead of
    // pretending the host rectangle necessarily contains an overflowing pseudo-element.
    for (const e of all) {
      if (!bounded()) break;
      if (!visible(e)) continue;
      for (const pseudo of ['::before', '::after']) {
        const pseudoStyle = getComputedStyle(e, pseudo);
        let content = pseudoStyle.content || '';
        if ([content, pseudoStyle.backgroundImage, pseudoStyle.borderImageSource, pseudoStyle.listStyleImage,
             pseudoStyle.maskImage, pseudoStyle.webkitMaskImage].some(value => /url\(/i.test(value || ''))) {
          fail('Generated CSS media needs manual handling.');
        }
        content = content.replace(/attr\(\s*([\w-]+)\s*\)/g, (_, name) => e.getAttribute(name) || '');
        if (content === 'none' || content === 'normal' || content === '""') continue;
        for (const expression of expressions) {
          expression.regex.lastIndex = 0;
          if (expression.regex.test(content.normalize('NFKC'))) { fail('Sensitive generated CSS content needs manual handling.'); break; }
        }
      }
    }
  } catch (_) {
    fail('DOM privacy collection failed.');
  }
  result.warnings.push('Rule-based detection can miss unknown personal text. Inspect the redacted image before sending.');
  result.warnings.push('Images, canvas, video and embedded frames are masked conservatively; no face/OCR model runs on screenshots.');
  return result;
}"""


def finite_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {label} in screenshot geometry.")
    return float(value)


def pixel_rect(
    rect: dict, *, scale_x: float, scale_y: float, width: int, height: int, padding: float
) -> dict | None:
    """Expand and clip a CSS/image rectangle without rounding away private edges."""
    if not isinstance(rect, dict):
        raise ValueError("Mask rectangles must be objects.")
    x = finite_number(rect.get("x"), "x")
    y = finite_number(rect.get("y"), "y")
    w = finite_number(rect.get("width", rect.get("w")), "width")
    h = finite_number(rect.get("height", rect.get("h")), "height")
    if w <= 0 or h <= 0:
        raise ValueError("Mask rectangles must have positive dimensions.")
    left = max(0, min(width, math.floor((x - padding) * scale_x)))
    top = max(0, min(height, math.floor((y - padding) * scale_y)))
    right = max(0, min(width, math.ceil((x + w + padding) * scale_x)))
    bottom = max(0, min(height, math.ceil((y + h + padding) * scale_y)))
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}
