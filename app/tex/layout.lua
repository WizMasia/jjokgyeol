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
local default_gap = tex.sp(tostring(policy.paragraph_spacing.default_pt) .. "pt")
local defaults = data.text_defaults
local roles = style.text_styles or {}
local note = {font_sp = defaults.note_sp, weight = defaults.weight, slant = defaults.slant}
if defaults.color:upper() ~= "#000000" then note.color = defaults.color end
for k, v in pairs(roles.note or {}) do note[k] = v end
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
  assert(type(name) == "string" and name:match("^[A-Za-z0-9][A-Za-z0-9_-]*%.[ot]tf$"), "invalid font basename")
  return name
end

local function sp(n)
  return string.format("%dsp", assert(math.tointeger(n)))
end

local function setup_frame_breaks()
  local after_frame = luatexbase.attributes.BookFrameEnd
  local hlist, penalty = node.id("hlist"), node.id("penalty")
  luatexbase.add_to_callback("buildpage_filter", function(group)
    -- A moved frame returns to the contribution list after TeX ships a page.
    if group ~= "penalty" and group ~= "after_output" then return end
    local pending = tex.lists.contrib_head
    for p in node.traverse_id(penalty, pending) do
      if node.has_attribute(p, after_frame) then
        local lines = node.count(hlist, tex.lists.page_head) + node.count(hlist, pending, p)
        -- When the frame is already first, moving it again gains no room.
        if lines == 1 then p.penalty = tex.count["@highpenalty"] end
      end
    end
  end, "jjokgyeol.frame_break")
end

function book.setup()
  local m, p = style.margins_sp, style.page_sp
  emit(string.format("\\geometry{%spaperwidth=%s,paperheight=%s,top=%s,right=%s,bottom=%s,left=%s,noheadfoot}",
    mirrored and "twoside," or "", sp(p[1]), sp(p[2]), sp(m[1]), sp(outer), sp(m[3]), sp(inner)))
  book.set_glyph = require("font_fallback")(policy, font_basename)
  if data.spacing then book.spacing = require("paragraph_spacing")(data.spacing, default_gap) end
  luatexbase.add_to_callback("pre_shipout_filter", require("font_text"), "jjokgyeol.font_text")
  local function font_pair(latin, hangul, name)
    local face = assert(policy.fonts[name])
    local regular, bold = font_basename(face.regular), font_basename(face.bold)
    local options = "[Path=fonts/,BoldFont=" .. bold .. ",ItalicFont=" .. regular ..
      ",UprightFeatures={RawFeature={fallback=bookregular}},BoldFeatures={RawFeature={fallback=bookbold}}" ..
      ",ItalicFeatures={FakeSlant=0.2,RawFeature={fallback=bookitalic}},BoldItalicFont=" .. bold ..
      ",BoldItalicFeatures={FakeSlant=0.2,RawFeature={fallback=bookbolditalic}},RawFeature={-tlig;-notdef},Ligatures=NoCommon,InterCharStretch=0pt,InterLatinCJK=0pt,InterHangul=0pt]{" .. regular .. "}"
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
  local styled_marks = false
  for _, fields in pairs(roles) do
    register(fields)
    for key in pairs(fields) do
      if key ~= "align" and key ~= "space_before_sp" and key ~= "space_after_sp" then styled_marks = true end
    end
  end
  for _, block in ipairs(data.blocks) do
    register(block.overrides)
    if block.kind == "table" and not book.tables then
      book.tables = require("table_layout")
      emit("\\newbox\\BookTableCell\\AddToHook{shipout/before}{\\directlua{book.tables.headers(tex.box.ShipoutBox)}}")
    end
  end
  register(note)
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
  if style.line_numbers then
    book.numbers = require("line_numbers")
    book.numbers.setup(style, width, left_at_page, policy.line_number_gap_pt, defaults.note_sp)
  end
  if has_color then
    emit("\\usepackage{xcolor}\\AddToHook{cmd/@makefntext/before}{\\normalcolor}")
  end
  if roles.heading1 and roles.heading1.frame == "box" then
    emit("\\usepackage{footnote}")
    setup_frame_breaks()
  end
  if next(data.footnotes) then
    emit("\\AddToHook{cmd/@makefntext/before}{\\directlua{book.note_style()}}")
    if styled_marks or defaults.weight ~= "normal" or defaults.slant ~= "normal" or defaults.heading_weight ~= "bold" then
      emit("\\AtBeginDocument{\\BookStyledMarks}")
    end
  end
end

local function appearance(values)
  if not values then return end
  if values.font then emit(assert(families[values.font])) end
  if values.weight then emit(values.weight == "bold" and "\\bfseries" or "\\mdseries") end
  if values.slant then emit(values.slant == "italic" and "\\itshape" or "\\upshape") end
  if values.color then
    local hex = assert(values.color:match("^#(%x%x%x%x%x%x)$"), "invalid RGB color")
    emit("\\color[HTML]{" .. hex .. "}")
  end
end

function book.note_style()
  appearance(note)
  emit("\\fontsize{" .. sp(note.font_sp) .. "}{\\baselineskip}\\selectfont")
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
local justified = style.paragraph_layout == "justified"
local measured = justified or style.paragraph_layout == "balanced"
  or style.ko_tracking_bounds_per_10000 ~= nil or data.paragraphs ~= nil
local paragraph = measured and require("paragraph")
local skipped_first
local lists = require("list_layout")
local wordbreak = require("wordbreak")
local fixed = require("fixed")
luatexbase.add_to_callback("pre_linebreak_filter", function(head)
  local candidate = active and data.candidate
  local aligned = tex.count.BookExplicitAlign == 1
  head = fixed.prepare(head, style, candidate, aligned)
  if paragraph_index and not active then
    head, skipped_first = paragraph.apply(head, style, paragraph_index, data.paragraphs, item and item.level)
  else
    head, skipped_first = wordbreak.apply(head, aligned, active, item and item.level)
  end
  return head
end, "jjokgyeol.wordspacing")
luatexbase.add_to_callback("post_linebreak_filter", function(head)
  head = fixed.finish(head)
  local start = head
  if skipped_first then
    for line in node.traverse_id(node.id("hlist"), head) do
      start = line.next
      -- Keep the empty indentation line with the first printed gloss line.
      local keep = node.new("penalty")
      keep.penalty = 10000
      head = node.insert_after(head, line, keep)
      break
    end
  end
  if item and item.first then lists.attach(start, item) end
  if justified then
    local em = font.getfont(font.current()).size
    if em > style.body_sp then
      -- Larger headings and scoped type can themselves open a page. Give
      -- their lines an em of ascent; keep existing interline baselines where
      -- the preceding baseline glue already provides that clearance.
      for line in node.traverse_id(node.id("hlist"), head) do
        if line.height < em then
          local prev = line.prev
          if prev and prev.id == node.id("glue") and prev.subtype == 2 then
            prev.width = math.max(0, prev.width - (em-line.height))
          end
          line.height = em
        end
      end
    end
  end
  if book.numbers and book.numbers.in_par then book.numbers.mark(start) end
  if book.running and book.running.in_par then book.running.lines(head) end
  return head
end, "jjokgyeol.line_spacing")

local gloss_style = roles.gloss or {}

function book.layout_error(reason)
  local marker = assert(io.open("layout-error.txt", "wb"))
  marker:write(reason)
  marker:close()
  error(reason)
end

function book.gloss()
  local surrounding = font.getfont(font.current()).size
  local size = gloss_style.font_sp
  if not size or size >= surrounding then
    size = math.min(surrounding - 1, math.max(1, (surrounding *
      defaults.gloss_per_10000 + fixed.RATIO_UNIT // 2) // fixed.RATIO_UNIT))
  end
  if size < 1 then
    book.layout_error("gloss_size")
  end
  appearance(gloss_style)
  emit("\\fontsize{" .. sp(size) .. "}{\\baselineskip}\\selectfont")
end

local marks, count = {}, 0
-- Each occurrence gets an even mark for its term and the next odd mark for its explanation.
local gloss_mark = 0
local end_indices, end_order = {}, {}
local function inlines(items, preserve_spaces)
  for _, item in ipairs(items) do
    if item.kind == "text" then
      literal(item.text, preserve_spaces)
    elseif item.kind == "running_section" then
      emit("\\directlua{book.running.section(" .. assert(math.tointeger(item.id)) .. ")}")
    elseif item.kind == "gloss" then
      gloss_mark = gloss_mark + 2
      emit("{\\attribute" .. wordbreak.gloss_attribute .. "=" .. gloss_mark .. "\\relax ")
      inlines(item.children, preserve_spaces)
      emit("\\attribute" .. wordbreak.gloss_attribute .. "=" .. (gloss_mark + 1) .. "\\relax \\directlua{book.gloss()}")
      literal("(" .. item.text .. ")", preserve_spaces)
      emit("}")
    elseif item.kind == "endnote_ref" then
      if not end_indices[item.id] then
        end_order[#end_order + 1] = item.id
        end_indices[item.id] = #end_order
      end
      emit("\\textsuperscript{")
      literal("미" .. end_indices[item.id])
      emit("}")
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
  emit("\\parskip=" .. sp(default_gap) .. "\\relax")
  if data.paragraphs and data.paragraphs.collect then
    assert(io.open("paragraph-candidates.jsonl", "w")):close()
  end
  if book.running then book.running.begin(literal, body_appearance) end
  -- TeX's stock first baseline is sized for 10pt. Reserve the publication
  -- body's em before the first baseline on every page, including continuations.
  if justified and tex.topskip.width < style.body_sp then
    emit("\\topskip=" .. sp(style.body_sp) .. "\\relax")
  end
  emit("\\fontsize{" .. sp(style.body_sp) .. "}{" .. sp(style.leading_sp) .. "}\\selectfont")
  lists.measure(data.blocks, literal, body_appearance)
  emit("\\directlua{book.blocks()}")
end

function book.capture_mark(index)
  lists.capture(data.blocks[index], style, width)
end

function book.item(index)
  item = data.blocks[index]
  if justified then paragraph_index = item and index end
end

local alignment = {left = "\\raggedright", center = "\\centering", right = "\\raggedleft"}
local function align_paragraph(value)
  emit(assert(alignment[value]) .. "\\BookExplicitAlign=1\\relax")
end

function book.blocks()
  lists.columns(data.blocks, width)
  if #data.blocks == 0 then emit("\\null") end
  for index, block in ipairs(data.blocks) do
    if book.spacing then book.spacing.begin_block(index) end
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
      local command = values.frame == "box" and "\\bookframedheading" or "\\bookheading"
      emit(command .. "{" .. sp(size) .. "}{" .. sp(leading) .. "}{")
      appearance(values)
      if block.preserve_spaces then emit("\\leavevmode") end
      inlines(block.inlines, block.preserve_spaces)
      if values.align then align_paragraph(values.align) end
      emit("}")
    elseif block.kind == "table" then
      book.tables.render(block, inlines)
    elseif block.kind == "list_item" then
      if #block.inlines == 0 then emit("\\noindent\\strut") end
      inlines(block.inlines, justified)
      local origin = lists.origin(block.group)
      local rest = origin + (values.rest_indent_sp or 0)
      local first = block.first and origin + (values.first_indent_sp or 0) or rest
      if math.max(first, rest) >= width then wordbreak.no_room(block.level) end
      emit(string.format("\\parshape 2 %s %s %s %s\\relax", sp(first), sp(width - first),
        sp(rest), sp(width - rest)))
      emit("\\directlua{book.item(" .. index .. ")}\\BookManuscriptPar\\directlua{book.item(0)}")
    elseif values.align then
      inlines(block.inlines, true)
      align_paragraph(values.align)
      emit("\\BookManuscriptPar")
    else
      assert(block.kind == "paragraph")
      inlines(block.inlines, measured)
      local first = values.first_indent_sp or style.first_indent_sp
      local rest = values.rest_indent_sp or style.rest_indent_sp
      emit(string.format("\\parshape 2 %s %s %s %s\\relax",
        sp(first), sp(width - first), sp(rest), sp(width - rest)))
      if data.candidate and index == data.candidate.block then
        emit("\\directlua{book.candidate(true)}\\BookManuscriptPar\\directlua{book.candidate(false)}")
      elseif measured then
        emit("\\directlua{book.paragraph(" .. index .. ")}\\BookManuscriptPar\\directlua{book.paragraph(nil)}")
      else
        emit("\\BookManuscriptPar")
      end
    end
    if grouped then emit("\\endgroup") end
  end
  if book.spacing then book.spacing.begin_block(0) end
  if #end_order > 0 then
    emit("\\begingroup\\bfseries ")
    literal("미주")
    emit("\\BookPar\\endgroup\\nobreak\\begingroup")
    book.note_style()
    for index, id in ipairs(end_order) do
      literal("미" .. index .. " " .. data.endnotes[id])
      emit("\\BookPar")
    end
    emit("\\endgroup")
  end
end

function book.paragraph(index)
  paragraph_index = index
end

function book.candidate(value)
  active = value
end
return book
