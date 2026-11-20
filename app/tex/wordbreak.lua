-- Select word boundaries on the shaped paragraph. TeX still packs each line,
-- distributes its bounded glue, and carries the original marks and insertions.
local fixed = require("fixed")
local read_codepoint = require("lua-uni-graphemes").read_codepoint
local glyph, glue, kern = node.id("glyph"), node.id("glue"), node.id("kern")
local penalty, disc = node.id("penalty"), node.id("disc")
local hlist, vlist, rule = node.id("hlist"), node.id("vlist"), node.id("rule")

local function measure(first, stop, state, commit)
  local width = 0
  local n = first
  while n ~= stop do
    local w, shrink
    if commit then
      fixed.apply(n, state)
    else
      local stretch
      w, stretch, shrink = fixed.measure(n, state)
    end
    if w then
      width = width + w - shrink
    elseif n.id == glue then
      width = width + n.width - n.shrink
    elseif n.id == kern then
      width = width + n.kern
    elseif n.id == disc then
      if n.replace then width = width + node.dimensions(n.replace) end
    elseif n.id == glyph or n.id == hlist or n.id == vlist or n.id == rule then
      width = width + n.width
    end
    n = n.next
  end
  return width
end

-- Use the line-break properties shipped with TeX for punctuation and no-break
-- characters. Alphabetic fallback still requires a Unicode grapheme boundary.
local ucd = require("lua-uni-parse")
local class = lpeg.P"OP" + "CL" + "CP" + "EX" + "IS" + "NS" + "GL" + "WJ"
local classes = ucd.parse_file("LineBreak",
  lpeg.Cg(ucd.fields(ucd.codepoint_range, lpeg.C(class))) + ucd.ignore_line, ucd.multiset)
local no_start = {CL=true, CP=true, EX=true, IS=true, NS=true, GL=true, WJ=true}
local function legal(left, right)
  return classes[left] ~= "OP" and classes[left] ~= "GL" and classes[left] ~= "WJ"
    and left ~= 0x2018 and left ~= 0x201C and not no_start[classes[right]]
    and right ~= 0x2019 and right ~= 0x201D
end

local gloss_attribute = luatexbase.new_attribute("jjokgyeol.gloss")

local function wordspace(n, literal_spaces)
  if n.id ~= glue or n.subtype ~= 13 and n.subtype ~= 14 then return false end
  if node.has_attribute(n, gloss_attribute) then return false end
  local left, right = n.prev, n.next
  if left and right and left.id == glyph and right.id == glyph then
    return legal(left.char, right.char)
  end
  if not literal_spaces then return true end
  local props = node.getproperty(n)
  if props and props.jjok_space_break ~= nil then return props.jjok_space_break end
  -- A run of literal spaces or a color switch can separate the punctuation
  -- from this glue. Classify the run once, including its interior spaces.
  local function transparent(p)
    return p and (p.id == penalty or p.id == kern or p.id == node.id("whatsit")
      or p.id == glue and (p.subtype == 13 or p.subtype == 14))
  end
  while transparent(left) do left = left.prev end
  while transparent(right) do right = right.next end
  local allowed = not (left and right and left.id == glyph and right.id == glyph)
    or legal(left.char, right.char)
  while n ~= right do
    if n.id == glue then
      props = node.getproperty(n) or {}
      props.jjok_space_break = allowed
      node.setproperty(n, props)
    end
    n = n.next
  end
  return allowed
end

local whatsit, colorstack = node.id("whatsit"), node.subtype("pdf_colorstack")

local function chunks(first, stop, annotated)
  local parts = {{first = first, gap = first}}
  local prev, gap, blocked, state
  local n = first
  while n ~= stop do
    if n.id == glyph then
      local c = n.char
      local new_cluster
      new_cluster, state = read_codepoint(c, state)
      if prev and not blocked and new_cluster
          and legal(prev, c) then
        parts[#parts + 1] = {first = n, gap = gap or n}
      end
      local mark = node.has_attribute(n, gloss_attribute)
      if mark then
        local part = parts[#parts]
        part.group = mark // 2
        part.term = part.term or mark % 2 == 0
      end
      prev, gap, blocked = c, nil, false
    elseif n.id == penalty or n.id == glue or n.id == kern then
      gap = gap or n
      blocked = blocked or n.id == penalty and n.penalty >= 10000
    elseif annotated and n.id == whatsit and n.subtype == colorstack then
      -- The color operation stays in the list and has no glyph or width.
      gap = nil
    elseif n.id == disc then
      -- A literal hyphen can be represented by a discretionary replacement.
      -- Keep its printed glyph with the preceding cluster.
      if n.replace then prev, gap, state = 45, nil, nil end
    else
      prev, gap, state = nil, nil, nil
    end
    n = n.next
  end
  return parts
end

local function no_room(level)
  local reason = level and ("list_width:" .. level) or "line_width"
  local marker = assert(io.open("layout-error.txt", "wb"))
  assert(marker:write(reason))
  assert(marker:close())
  error(reason)
end

local function lock(head)
  -- Keep only the selected edges. A penalty immediately before glue prevents
  -- TeX from independently splitting a word it can place on the next line.
  for item in node.traverse(head) do
    if item.id == penalty and item.penalty > -10000 or item.id == disc then
      item.penalty = 10000
    elseif item.id == glue and item.prev and item.prev.id ~= penalty then
      local p = node.new(penalty)
      p.penalty = 10000
      head = node.insert_before(head, item, p)
    end
  end
  return head
end

local function apply(head, style, active, level)
  if active then return head end
  local shape = tex.parshape
  local skipped_first = false
  local line, used, state, occupied = 1, 0, {}, false
  local function available(i)
    local width = shape and shape[math.min(i, #shape)][2] or tex.hsize
    return width - tex.leftskip.width - tex.rightskip.width
  end
  local function newline(at)
    local p = node.new(penalty)
    p.penalty = -10000
    head = node.insert_before(head, at, p)
    line, used, state, occupied = line + 1, 0, {}, false
  end
  local function fits(first, stop)
    local trial = {}
    for k, v in pairs(state) do trial[k] = v end
    return used + measure(first, stop, trial, false) <= available(line)
  end
  local function keep(first, stop)
    used = used + measure(first, stop, state, true)
    occupied = true
  end
  local function try_place(gap, first, stop, can_move)
    if fits(gap, stop) then
      keep(gap, stop)
      return true
    end
    if can_move and measure(first, stop, {}, false) <= available(line + 1) then
      if not occupied then
        -- Keep the paragraph start and its indentation box on the short first line.
        skipped_first = true
        gap = head.next
        if gap and gap.id == hlist and gap.subtype == 3 then gap = gap.next end
      end
      newline(gap)
      keep(first, stop)
      return true
    end
    return false
  end

  local function place(gap, first, stop, annotated)
    if try_place(gap, first, stop, occupied and not annotated) then return end
    local parts = chunks(first, stop, annotated)
    parts[1].gap = gap
    local function take(i, last)
      local limit = parts[last + 1] and parts[last + 1].gap or stop
      local wider_next = parts[i].group and line == 1 and available(2) > available(1)
      return try_place(parts[i].gap, parts[i].first, limit, occupied or wider_next)
    end

    local i = 1
    while i <= #parts do
      local group, last = parts[i].group, i
      while parts[last + 1] and parts[last + 1].group == group do last = last + 1 end
      -- A legal chunk may include punctuation on either side of the marked glyphs.
      if annotated and take(i, last) then i = last + 1 end
      if group and i <= last and parts[i].term then
        local term = i
        while term < last and parts[term + 1].term do term = term + 1 end
        if term < last and take(i, term + 1) then
          i = term + 2
        elseif take(i, term) then
          i = term + 1
        end
      end
      while i <= last do
        if not take(i, i) then no_room(level) end
        i = i + 1
      end
    end
  end

  local gap, first, annotated
  local n = head
  while n do
    local forced = n.id == penalty and n.penalty <= -10000
    if wordspace(n) or forced or n.id == glue and n.subtype == 15 then
      if first then place(gap, first, n, annotated) end
      annotated = nil
      gap, first = first and n or gap or n, nil
      if forced then
        line, used, state, occupied = line + 1, 0, {}, false
        gap = nil
      end
    else
      first, gap = first or n, gap or n
      if n.id == glyph then annotated = annotated or node.has_attribute(n, gloss_attribute) end
    end
    n = n.next
  end
  if first then place(gap, first, nil, annotated) end

  return lock(head), skipped_first
end

return {apply = apply, no_room = no_room, wordspace = wordspace, chunks = chunks, lock = lock,
        gloss_attribute = gloss_attribute}
