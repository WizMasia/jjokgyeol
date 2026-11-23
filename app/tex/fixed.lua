-- Scaled points only. Carry the division remainder, not a running product
-- proportional to manuscript length. Halfway values round away from zero.
local fixed = {RATIO_UNIT = 10000}

-- A whole line uses the same rounding as its individual carried advances.
function fixed.scaled(base, ratio, carry)
  local r = math.abs(ratio)
  local tail = (base % fixed.RATIO_UNIT) * r + (carry or fixed.RATIO_UNIT // 2)
  local sp = (base // fixed.RATIO_UNIT) * r + tail // fixed.RATIO_UNIT
  return ratio < 0 and -sp or sp, tail % fixed.RATIO_UNIT
end

local function scale(state, channel, base, ratio)
  local sp, carry = fixed.scaled(base, ratio, state[channel])
  state[channel] = carry
  return sp
end

local glyph, glue = node.id("glyph"), node.id("glue")
local kern, penalty = node.id("kern"), node.id("penalty")

-- luatexko supplies the breakable inter-cluster glue on both supported TeX
-- releases. Never insert a kern beside that glue or inside a shaped glyph.
local function cluster_start(c)
  return c >= 0xAC00 and c <= 0xD7A3 or c >= 0x1100 and c <= 0x115F
    or c >= 0xA960 and c <= 0xA97C or c >= 0x3131 and c <= 0x318E
end

local function tag(n, spec)
  local p = node.getproperty(n) or {}
  p.jjok_spacing = spec
  node.setproperty(n, p)
end

function fixed.spacing(n)
  local p = node.getproperty(n)
  return p and p.jjok_spacing
end

-- A fresh rounding state starts each candidate line.
function fixed.measure(n, state)
  local s = fixed.spacing(n)
  if not s then return end
  if s.tracking then
    return scale(state, "tracking", s.base, s.tracking), 0, 0
  end
  local width = scale(state, "space", s.base, s.ratio)
  if s.low == s.high then return width, 0, 0 end
  -- Round the actual bounds. Independently rounding (target - minimum)
  -- can let TeX squeeze a line one sp below the measured minimum.
  local low = scale(state, "low", s.base, s.low)
  local high = scale(state, "high", s.base, s.high)
  width = math.max(low, math.min(width, high))
  return width, math.max(0, high - width), width - low
end

function fixed.apply(n, state)
  local width, stretch, shrink = fixed.measure(n, state)
  if width then
    local changed = n.width ~= width or n.stretch ~= stretch or n.shrink ~= shrink
    n.width, n.stretch, n.shrink = width, stretch, shrink
    return changed
  end
end

function fixed.prepare(head, style, candidate, aligned)
  local low, high = table.unpack(style.space_per_10000)
  if candidate then low, high = candidate.space_per_10000, candidate.space_per_10000 end
  local ratio = math.max(low, math.min(fixed.RATIO_UNIT, high))
  if aligned then low, high = ratio, ratio end
  local tracking = candidate and candidate.ko_tracking_per_10000 or style.ko_tracking_per_10000
  local state, previous, boundary = {}, false, nil
  local space_no, next_break = 0, 1
  for n in node.traverse(head) do
    if n.id == glyph then
      local c = n.char
      if cluster_start(c) then
        if previous and boundary and (tracking ~= 0 or style.ko_tracking_bounds_per_10000) then
          tag(boundary, {base = font.getfont(n.font).size, tracking = tracking})
          fixed.apply(boundary, state)
        end
        previous = true
      elseif not (c >= 0x1160 and c <= 0x11FF or c >= 0xD7B0 and c <= 0xD7FB
          or c >= 0x300 and c <= 0x36F or c >= 0xFE00 and c <= 0xFE0F) then
        previous = false
      end
      boundary = nil
    elseif n.id == glue and (n.subtype == 13 or n.subtype == 14) then
      tag(n, {base = n.width, ratio = ratio, low = low, high = high})
      fixed.apply(n, state)
      if candidate then
        space_no = space_no + 1
        if candidate.breaks[next_break] == space_no then
          local edge = node.new(penalty)
          edge.penalty = -10000
          head = node.insert_before(head, n, edge)
          state, next_break = {}, next_break + 1
        end
      end
      previous, boundary = false, nil
    elseif n.id == glue and n.subtype == 0 and n.width == 0 then
      boundary = n
    elseif n.id ~= penalty and n.id ~= kern then
      previous, boundary = false, nil
    end
  end
  return head
end

function fixed.finish(head)
  for line in node.traverse_id(node.id("hlist"), head) do
    local state, changed = {}, false
    for n in node.traverse_id(glue, line.head) do
      changed = fixed.apply(n, state) or changed
    end
    if changed then
      -- Refresh the line's glue setting after distributing the last sp.
      local packed = node.hpack(line.head, line.width, "exactly", line.dir)
      line.glue_set, line.glue_sign, line.glue_order =
        packed.glue_set, packed.glue_sign, packed.glue_order
      packed.head = nil
      node.free(packed)
    end
  end
  return head
end

return fixed
