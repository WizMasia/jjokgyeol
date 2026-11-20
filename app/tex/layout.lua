-- Only template-owned strings go through TeX's command tokenizer.
local data = assert(utilities.json.load("input.json"))
local style, policy = data.style, data.policy
local mirrored = style.gutter_sp ~= nil
local inner = style.margins_sp[4] + (style.gutter_sp or 0)
local outer = style.margins_sp[2]
local width = style.page_sp[1] - inner - outer
local function left_at_page(page)
  return mirrored and page % 2 == 0 and outer or inner
end
local book = {}
local emit = tex.sprint
local families = {}
local compose = require("lua-uni-normalize").NFC
local function literal(text, preserve_spaces)
  text = compose(text)
  if preserve_spaces then
    for word, spaces in text:gmatch("([^ ]*)( *)") do
      tex.sprint(-2, word)
      emit(string.rep("\\space{}", #spaces))
    end
  else
    tex.sprint(-2, text) -- catcode 10 for spaces and 12 (other) for every other character
  end
end

local function font_basename(name)
  assert(type(name) == "string" and name:match("^[A-Za-z0-9][A-Za-z0-9_-]*%.ttf$"), "invalid font basename")
  return name
end

local function sp(n)
  return string.format("%dsp", assert(math.tointeger(n)))
end

function book.setup()
  local m, p = style.margins_sp, style.page_sp
  emit(string.format("\\geometry{%spaperwidth=%s,paperheight=%s,top=%s,right=%s,bottom=%s,left=%s,noheadfoot}",
    mirrored and "twoside," or "", sp(p[1]), sp(p[2]), sp(m[1]), sp(outer), sp(m[3]), sp(inner)))
  local function font_pair(latin, hangul, name)
    local face = assert(policy.fonts[name])
    local regular, bold = font_basename(face.regular), font_basename(face.bold)
    local options = "[Path=fonts/,BoldFont=" .. bold .. ",ItalicFont=" .. regular ..
      ",ItalicFeatures={FakeSlant=0.2},BoldItalicFont=" .. bold ..
      ",BoldItalicFeatures={FakeSlant=0.2},RawFeature=-tlig,Ligatures=NoCommon,InterCharStretch=0pt,InterLatinCJK=0pt,InterHangul=0pt]{" .. regular .. "}"
    emit(latin .. options .. hangul .. options)
  end
  font_pair("\\setmainfont", "\\setmainhangulfont", style.body_font)
  font_pair("\\setsansfont", "\\setsanshangulfont", style.heading_font)
  families[style.heading_font] = "\\sffamily"
  families[style.body_font] = "\\rmfamily"
  local extra_fonts, has_color = 0, false
  local function register(values)
    if values then
      has_color = has_color or values.color
      if values.font and not families[values.font] then
        extra_fonts = extra_fonts + 1
        local suffix = string.rep("A", extra_fonts)
        local latin, hangul = "\\BookLatin" .. suffix, "\\BookHangul" .. suffix
        font_pair("\\newfontfamily" .. latin, "\\newhangulfontfamily" .. hangul, values.font)
        families[values.font] = latin .. hangul
      end
    end
  end
  for _, block in ipairs(data.blocks) do register(block.overrides) end
  if data.running_sections then
    register(data.running_sections.defaults)
    for _, scope in ipairs(data.running_sections.scopes) do
      for _, role in ipairs({"header", "footer"}) do
        for _, fields in pairs(scope[role]) do register(fields) end
      end
    end
    book.running = require("running_heads")
    book.running.setup(data.running_sections, style, width, left_at_page)
  end
  if has_color then
    emit("\\usepackage{xcolor}\\AddToHook{cmd/@makefntext/before}{\\normalcolor}")
  end
end

local function appearance(values)
  if not values then return end
  if values.font then emit(assert(families[values.font])) end
  if values.color then
    local hex = assert(values.color:match("^#(%x%x%x%x%x%x)$"), "invalid RGB color")
    emit("\\color[HTML]{" .. hex .. "}")
  end
end

local function body_appearance(values)
  appearance(values)
  if values and (values.font_sp or values.leading_sp) then
    emit("\\fontsize{" .. sp(values.font_sp or style.body_sp)
      .. "}{" .. sp(values.leading_sp or style.leading_sp) .. "}\\selectfont")
  end
end

-- Space nodes are measured after shaping. TeX still builds the lines and moves
-- insertions (including footnotes) through its normal page builder.
local active, item, paragraph_index = false, nil, nil
local paragraph = require("paragraph")
local lists = require("list_layout")
local wordbreak = require("wordbreak")
local fixed = require("fixed")
luatexbase.add_to_callback("pre_linebreak_filter", function(head)
  local candidate = active and data.candidate
  head = fixed.prepare(head, style, candidate)
  if paragraph_index and not active then
    return paragraph.apply(head, style, paragraph_index, data.paragraphs)
  end
  return wordbreak.apply(head, style, active, item and item.level)
end, "jjokgyeol.wordspacing")
luatexbase.add_to_callback("post_linebreak_filter", function(head)
  head = fixed.finish(head)
  if item and item.first then lists.attach(head, item) end
  if book.running and book.running.in_par then book.running.lines(head) end
  return head
end, "jjokgyeol.line_spacing")

local marks, count = {}, 0
local function inlines(items, preserve_spaces)
  for _, item in ipairs(items) do
    if item.kind == "text" then
      literal(item.text, preserve_spaces)
    elseif item.kind == "running_section" then
      emit("\\directlua{book.running.section(" .. assert(math.tointeger(item.id)) .. ")}")
    elseif item.kind == "note_ref" then
      if marks[item.id] then
        emit("\\footnotemark[" .. marks[item.id] .. "]")
      else
        count = count + 1
        marks[item.id] = count
        emit("\\footnote{")
        literal(assert(data.footnotes[item.id]))
        emit("}")
      end
    else
      local command = assert(({strong="\\textbf{", emphasis="\\emph{"})[item.kind])
      emit(command)
      inlines(item.children, preserve_spaces)
      emit("}")
    end
  end
end

local function scaled_size(size, per_10000)
  return (size * per_10000 + fixed.RATIO_UNIT // 2) // fixed.RATIO_UNIT
end

function book.document()
  if data.paragraphs and data.paragraphs.collect then
    assert(io.open("paragraph-candidates.jsonl", "w")):close()
  end
  if book.running then book.running.begin(literal, body_appearance) end
  emit("\\fontsize{" .. sp(style.body_sp) .. "}{" .. sp(style.leading_sp) .. "}\\selectfont")
  lists.measure(data.blocks, literal, body_appearance)
  emit("\\directlua{book.blocks()}")
end

function book.capture_mark(index)
  lists.capture(data.blocks[index], style, width)
end

function book.item(index)
  item = data.blocks[index]
end

function book.blocks()
  lists.columns(data.blocks, width)
  if #data.blocks == 0 then emit("\\null") end
  for index, block in ipairs(data.blocks) do
    if book.running then emit("\\directlua{book.running.section(" .. block.running_id .. ")}") end
    local values = block.overrides or {}
    local grouped = block.kind ~= "heading" and next(values) ~= nil
    if grouped then
      emit("\\begingroup")
      body_appearance(values)
    end
    if block.kind == "heading" then
      local size = values.font_sp or scaled_size(style.body_sp, policy.heading_per_10000[block.level])
      local leading = values.leading_sp or scaled_size(size, policy.heading_leading_per_10000)
      emit("\\bookheading{" .. sp(size) .. "}{" .. sp(leading) .. "}{")
      appearance(values)
      if block.preserve_spaces then emit("\\leavevmode") end
      inlines(block.inlines, block.preserve_spaces)
      emit("}")
    elseif block.kind == "list_item" then
      if #block.inlines == 0 then emit("\\noindent\\strut") end
      inlines(block.inlines)
      local origin = lists.origin(block.group)
      local rest = origin + (values.rest_indent_sp or 0)
      local first = block.first and origin + (values.first_indent_sp or 0) or rest
      if math.max(first, rest) >= width then wordbreak.no_room(block.level) end
      emit(string.format("\\parshape 2 %s %s %s %s\\relax", sp(first), sp(width - first),
        sp(rest), sp(width - rest)))
      emit("\\directlua{book.item(" .. index .. ")}\\BookPar\\directlua{book.item(0)}")
    else
      assert(block.kind == "paragraph")
      inlines(block.inlines, true)
      local first = values.first_indent_sp or style.first_indent_sp
      local rest = values.rest_indent_sp or style.rest_indent_sp
      emit(string.format("\\parshape 2 %s %s %s %s\\relax",
        sp(first), sp(width - first), sp(rest), sp(width - rest)))
      if data.candidate and index == data.candidate.block then
        emit("\\directlua{book.candidate(true)}\\BookPar\\directlua{book.candidate(false)}")
      else
        emit("\\directlua{book.paragraph(" .. index .. ")}\\BookPar\\directlua{book.paragraph(nil)}")
      end
    end
    if grouped then emit("\\endgroup") end
  end
end

function book.paragraph(index)
  paragraph_index = index
end

function book.candidate(value)
  active = value
end
return book
