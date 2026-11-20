-- Compare complete layouts on the original shaped list. Only the winner changes nodes.
local fixed = require("fixed")
local breaks = require("wordbreak")
local glyph, disc = node.id("glyph"), node.id("disc")
local glue, penalty = node.id("glue"), node.id("penalty")
local unit = fixed.RATIO_UNIT
local ucd = require("lua-uni-parse")
local whitespace = ucd.parse_file("PropList", lpeg.Cg(ucd.fields(ucd.codepoint_range,
  lpeg.P"White_Space" * lpeg.Cc(true))) + ucd.ignore_line, ucd.multiset)

local function width(a, b, tracking, space)
  return b.width - a.width + fixed.scaled(b.tracking - a.tracking, tracking)
    + fixed.scaled(b.space - a.space, space)
end

local function scan(first, stop, prior)
  local m = {width=prior.width, tracking=prior.tracking, space=prior.space, glyphs=prior.glyphs}
  local left, right
  local n = first
  while n ~= stop do
    local spacing = fixed.spacing(n)
    if spacing then
      local key = spacing.tracking and "tracking" or "space"
      m[key] = m[key] + spacing.base
    else
      m.width = m.width + node.dimensions(n, n.next)
    end
    if n.id == glyph and not whitespace[n.char] then
      left, right, m.glyphs = left or n.char, n.char, m.glyphs + 1
    elseif n.id == disc and n.replace then
      for g in node.traverse_id(glyph, n.replace) do
        if not whitespace[g.char] then
          left, right, m.glyphs = left or g.char, g.char, m.glyphs + 1
        end
      end
    end
    n = n.next
  end
  return m, left, right
end

-- Each boundary keeps cumulative shaped advances before and after its gap.
-- A range can then be tried in O(1) with fresh line-local rounding.
local function boundaries(head, shapes, low_tracking, low_space)
  local parts, total = {}, {width=0, tracking=0, space=0, glyphs=0}
  local function word(gap, first, stop)
    local before = scan(gap, first, total)
    local after, left, right = scan(first, stop, before)
    local start = #parts + 1
    if width(before, after, low_tracking, low_space) > math.min(shapes[1].width, shapes[2].width) then
      local chunks = breaks.chunks(first, stop)
      for i, chunk in ipairs(chunks) do
        local limit = chunks[i+1] and chunks[i+1].gap or stop
        if i > 1 then before = scan(chunk.gap, chunk.first, total) end
        total, left, right = scan(chunk.first, limit, before)
        parts[#parts+1] = {gap=i == 1 and gap or chunk.gap, first=chunk.first,
          before=before, after=total, left=left, right=right, word_start=start}
      end
    else
      total = after
      parts[start] = {gap=gap, first=first, before=before, after=after,
        left=left, right=right, word_start=start}
    end
    for i=start,#parts do parts[i].word_end = #parts end
  end
  local gap, first = head, nil
  for n in node.traverse(head) do
    if n.id == glyph and node.has_attribute(n, breaks.gloss_attribute) then return end
    if n.id == penalty and n.penalty <= -10000 then return end
    if breaks.wordspace(n, true) or n.id == glue and n.subtype == 15 then
      if first then
        word(gap, first, n)
        gap, first = n, nil
      end
    else
      first = first or n
    end
  end
  if first then word(gap, first, nil) end
  return parts
end

local function geometry(line)
  local shape = tex.parshape
  local indent, available = 0, tex.hsize
  if shape then indent, available = table.unpack(shape[math.min(line, #shape)]) end
  return {start=indent + tex.leftskip.width,
          width=available - tex.leftskip.width - tex.rightskip.width}
end

local function line_ends(parts, shapes, tracking, space, low_tracking, low_space)
  local ends, start = {}, 1
  while start <= #parts do
    local limit = shapes[#ends == 0 and 1 or 2].width
    local i = start
    while i <= #parts do
      local last = parts[i].word_end
      if width(parts[start].before, parts[last].after, tracking, space) <= limit then
        i = last + 1
      elseif i > start and width(parts[i].before, parts[last].after, low_tracking, low_space)
          <= shapes[2].width then
        break
      else
        -- Only split when the whole word cannot fit the next line (or this
        -- empty first line) even at the actual style minima.
        if width(parts[i].before, parts[last].after, low_tracking, low_space) <= limit
            and i == start then return end
        while i <= last and width(parts[start].before, parts[i].after, tracking, space) <= limit do
          i = i + 1
        end
        break
      end
    end
    if i == start then return end
    ends[#ends+1], start = i - 1, i
  end
  return ends
end

local function highest_fit(low, high, fits)
  while low < high do
    local mid = low + (high - low + 1) // 2
    if fits(mid) then low = mid else high = mid - 1 end
  end
  return low
end

local function candidate(parts, shapes, ends, style, spread)
  local base = style.ko_tracking_per_10000
  local bounds = style.ko_tracking_bounds_per_10000 or {base, base}
  local low_space, high_space = table.unpack(style.space_per_10000)
  local natural = math.max(low_space, math.min(unit, high_space))
  local lines, keys, cost = {}, {}, 0
  local start, previous_t, previous_s = 1, base, natural
  for i, last in ipairs(ends) do
    local a, b = parts[start], parts[last]
    local shape = shapes[math.min(i, 2)]
    local function fits(t, s) return width(a.before, b.after, t, s) <= shape.width end
    if not fits(bounds[1], low_space) then return end
    local t, s = base, natural
    if not fits(t, s) then
      if not fits(t, low_space) then
        t = highest_fit(bounds[1], base, function(v) return fits(v, low_space) end)
      end
      s = highest_fit(low_space, natural, function(v) return fits(t, v) end)
    elseif spread and i < #ends then
      if b.after.space > a.before.space then
        s = highest_fit(natural, high_space, function(v) return fits(t, v) end)
      end
      if b.after.tracking > a.before.tracking then
        t = highest_fit(base, bounds[2], function(v) return fits(v, s) end)
      end
    end
    local remaining = shape.width - width(a.before, b.after, t, s)
    local dt, ds = t - previous_t, s - previous_s
    local line = {end_glyph=b.after.glyphs, first_char=a.left, last_char=b.right,
      start_sp=shape.start, available_sp=shape.width, remaining_sp=remaining,
      tracking_per_10000=t, space_per_10000=s, tracking_delta=t-base, space_deviation=s-unit,
      tracking_change=i == 1 and 0 or dt, space_change=i == 1 and 0 or ds,
      split_word=last ~= b.word_end}
    lines[i] = line
    keys[i] = table.concat({last, t, s, shape.start, shape.width, remaining}, "/")
    local ragged = remaining / shape.width
    if i < #ends then cost = cost + ragged * ragged end
    cost = cost + (math.abs(t-base) + math.abs(s-natural) +
      math.abs(line.tracking_change) + math.abs(line.space_change)) / unit
    previous_t, previous_s, start = t, s, last + 1
  end
  local orphan = #lines > 1 and lines[#lines].end_glyph - lines[#lines-1].end_glyph == 1
  return {ends=ends, lines=lines, key=table.concat(keys, ";"), orphan=orphan, cost=cost}
end

local function better(a, b)
  if not b or #a.lines ~= #b.lines then return not b or #a.lines < #b.lines end
  if a.orphan ~= b.orphan then return not a.orphan end
  return a.cost < b.cost
end

-- Check intermediate glyph/box edges too: negative tracking or a kern can
-- put an earlier glyph outside the measure even when the final advance fits.
local function check_nodes(parts, choice, collect)
  local start = 1
  for i, last in ipairs(choice.ends) do
    local line = choice.lines[i]
    local stop = parts[last+1] and parts[last+1].gap
    local x, tc, sc = 0, nil, nil
    local n = parts[start].first
    while n ~= stop do
      local spec, w = fixed.spacing(n)
      if spec and spec.tracking then
        w, tc = fixed.scaled(spec.base, line.tracking_per_10000, tc)
      elseif spec then
        w, sc = fixed.scaled(spec.base, line.space_per_10000, sc)
        if collect then
          line.spaces_sp = line.spaces_sp or {}
          line.spaces_sp[#line.spaces_sp+1] = w
        end
      else
        w = node.dimensions(n, n.next)
        if w > 0 and (x < 0 or x + w > line.available_sp) then return false end
      end
      x = x + w
      n = n.next
    end
    if x ~= line.available_sp - line.remaining_sp then return false end
    start = last + 1
  end
  return true
end

local function pack(head, parts, choice)
  local start = 1
  for i, last in ipairs(choice.ends) do
    local line = choice.lines[i]
    local next_part = parts[last+1]
    local stop = next_part and next_part.gap
    local state, n = {}, parts[start].first
    while n ~= stop do
      local spacing = fixed.spacing(n)
      if spacing then
        if spacing.tracking then spacing.tracking = line.tracking_per_10000
        else
          local s = line.space_per_10000
          spacing.ratio, spacing.low, spacing.high = s, s, s
        end
        fixed.apply(n, state)
      end
      n = n.next
    end
    if stop then
      local edge = node.new(penalty)
      edge.penalty = -10000
      head = node.insert_before(head, stop, edge)
    end
    start = last + 1
  end
  return breaks.lock(head)
end

local function apply(head, style, block, request)
  local shapes = {geometry(1), geometry(2)}
  local base = style.ko_tracking_per_10000
  local low = style.ko_tracking_bounds_per_10000 and style.ko_tracking_bounds_per_10000[1] or base
  local low_space, high_space = table.unpack(style.space_per_10000)
  local natural = math.max(low_space, math.min(unit, high_space))
  local parts = boundaries(head, shapes, low, low_space)
  if not parts or #parts == 0 then return breaks.apply(head, true) end
  local options, seen, rule, replay = {}, {}
  local function consider(ends, spread)
    local option = candidate(parts, shapes, ends, style, spread)
    if not option or seen[option.key] then return end
    if not check_nodes(parts, option, request and request.collect) then return end
    seen[option.key] = true
    if better(option, rule) then rule = option end
    if request then
      local id = md5.sumhexa(request.snapshot .. "/" .. block .. "/" .. option.key)
      option.id = id
      if request.collect then options[#options+1] = {id=id, lines=option.lines} end
      if id == request.choices[tostring(block)] then replay = option end
    end
  end
  local function compare(t, s, spread)
    local ends = line_ends(parts, shapes, t, s, low, low_space)
    if not ends then return end
    consider(ends, spread)
    if #ends < 2 then return end
    local p = #ends - 1
    local earlier = parts[ends[p]].word_start - 1
    if earlier <= (ends[p-1] or 0) or parts[ends[p]].word_end ~= ends[p] then return end
    local alternate = {}
    for i, last in ipairs(ends) do alternate[i] = last end
    alternate[p] = earlier
    consider(alternate, spread)
  end
  compare(base, natural)
  if low_space ~= high_space or style.ko_tracking_bounds_per_10000 then compare(low, low_space, true) end
  if not rule then breaks.no_room() end
  local choice = replay or rule
  if request and request.collect then
    local row = {block=block, candidates=options, rule_id=rule.id, selected_id=choice.id}
    local file = assert(io.open("paragraph-candidates.jsonl", "a"))
    assert(file:write(utilities.json.tostring(row), "\n"))
    assert(file:close())
  end
  return pack(head, parts, choice)
end

return {apply=apply}
