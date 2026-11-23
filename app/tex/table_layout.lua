-- Shape each cell once at natural width, then unbox it into a paragraph at
-- its allocated width. The normal page builder handles rows and footnotes.
local tables = {}
local emit = tex.sprint
local hlist, vlist, glyph = node.id("hlist"), node.id("vlist"), node.id("glyph")
local insert = node.id("ins")
local cells, widths, preferred, gap, rows
local headers = {}
local header_slot = luatexbase.new_attribute("jjokgyeol.table_header")
local hfill = "0pt plus 1fil"

function tables.render(block, inlines)
  emit("\\begingroup\\directlua{book.tables.begin()}")
  for r, row in ipairs(block.rows) do
    for c, spans in ipairs(row) do
      emit("\\setbox\\BookTableCell=\\hbox{")
      if r == 1 then emit("\\bfseries ") end
      inlines(spans)
      emit(string.format("}\\directlua{book.tables.measure(%d,%d)}", r, c))
    end
  end
  emit("\\directlua{book.tables.allocate()}")
  for r, row in ipairs(block.rows) do
    for c in ipairs(row) do
      local align = block.alignments[c]
      local left = align == "left" and "0pt" or hfill
      local right = align == "right" and "0pt" or hfill
      emit(string.format("\\setbox\\BookTableCell=\\vbox{\\directlua{book.tables.restore(%d,%d)}", r, c))
      emit("\\leftskip=" .. left .. "\\relax\\rightskip=" .. right .. "\\relax\\parfillskip=0pt" ..
        "\\parindent=0pt\\noindent\\unhbox\\BookTableCell\\BookPar}")
      emit(string.format("\\directlua{book.tables.collect(%d,%d)}", r, c))
    end
  end
  emit("\\directlua{book.tables.paginate(\\number\\dimexpr\\skip\\footins\\relax)}\\endgroup")
end

function tables.begin()
  cells, widths, preferred, rows = {}, {}, {}, {}
  gap = font.getfont(font.current()).size
end

function tables.measure(r, c)
  local box = tex.box.BookTableCell
  local natural, minimum = 0, widths[c] or 1
  -- TeX's packed width can overflow for a manuscript-length unbroken word.
  for n in node.traverse(box.head) do
    natural = natural + node.dimensions(n, n.next)
    if n.id == glyph or n.id == hlist or n.id == vlist then
      minimum = math.max(minimum, n.width)
    end
  end
  widths[c], preferred[c] = minimum, math.max(natural, preferred[c] or 0)
  cells[r] = cells[r] or {}
  cells[r][c] = node.copy(box)
end

function tables.allocate()
  local order = {}
  local room = tex.hsize - (#widths - 1) * gap
  for c, min in ipairs(widths) do
    room = room - min
    order[c] = c
  end
  if room < 0 then book.layout_error("table_width") end
  -- Satisfy compact columns first. The remaining columns share the spare
  -- width, with the widest receiving any final surplus or rounding remainder.
  local function demand(c) return math.max(0, preferred[c] - widths[c]) end
  table.sort(order, function(a, b)
    if demand(a) == demand(b) then return a < b end
    return demand(a) < demand(b)
  end)
  for i, c in ipairs(order) do
    local extra = math.min(demand(c), room // (#order - i + 1))
    if i == #order then extra = room end
    widths[c], room = widths[c] + extra, room - extra
  end
end

function tables.restore(r, c)
  tex.hsize = widths[c]
  tex.box.BookTableCell = cells[r][c]
  cells[r][c] = nil
end

local whatsit, colorstack = node.id("whatsit"), node.subtype("pdf_colorstack")
local function cell_line(original, colors)
  local line = node.copy(original)
  local first = line.head
  -- A gloss can open a PDF color on one line and close it on the next.
  -- Balance each line before interleaving it with lines from other cells.
  for i = #colors, 1, -1 do
    line.head = node.insert_before(line.head, line.head, node.copy(colors[i]))
  end
  for n in node.traverse_id(whatsit, first) do
    if n.subtype == colorstack then
      if n.command == 1 then colors[#colors + 1] = n
      elseif n.command == 2 then colors[#colors] = nil end
    end
  end
  local tail = node.tail(line.head)
  for i = #colors, 1, -1 do
    local pop = node.new(whatsit, colorstack)
    pop.stack, pop.command = colors[i].stack, 2
    line.head, tail = node.insert_after(line.head, tail, pop)
  end
  return line
end

function tables.collect(r, c)
  -- Even a completely empty row occupies one line of the table.
  local row = rows[r] or {lines = {{}}, notes = {}}
  rows[r] = row
  local i, colors = 0, {}
  for n in node.traverse(tex.box.BookTableCell.head) do
    if n.id == hlist then
      i = i + 1
      row.lines[i] = row.lines[i] or {}
      row.lines[i][c] = cell_line(n, colors)
    elseif n.id == insert then
      -- TeX migrates a footnote insertion just below the broken line that
      -- owns its mark. Return it to the page builder with that line's row.
      row.notes[i] = row.notes[i] or {}
      table.insert(row.notes[i], node.copy(n))
    end
  end
end

local function append(head, tail, n)
  if tail then tail.next, n.prev = n, tail end
  return head or n, n
end

local function strip(line)
  local head, tail
  for c, width in ipairs(widths) do
    if line[c] then head, tail = append(head, tail, line[c]) end
    local space = (line[c] and 0 or width) + (c < #widths and gap or 0)
    if space > 0 then
      local kern = node.new("kern")
      kern.kern = space
      head, tail = append(head, tail, kern)
    end
  end
  local box = node.hpack(head)
  local strut = tex.box.strutbox
  box.height, box.depth = math.max(box.height, strut.height), math.max(box.depth, strut.depth)
  return box
end

-- Each breakable unit reserves its header above the data. Between units a
-- negative skip cancels that space on the same page. At a page break TeX
-- discards the skip, so the first unit has exactly enough room for its header.
function tables.headers(page)
  local seen = {}
  local function visit(head)
    for n in node.traverse(head) do
      if n.id == hlist or n.id == vlist then
        local id = node.has_attribute(n, header_slot)
        if id then
          if not seen[id] then n.head = node.copy_list(headers[id].head) end
          seen[id] = true
        else
          visit(n.head)
        end
      end
    end
  end
  visit(page.head)
end

-- Keep the notes attached to a breakable unit in manuscript order. One TeX
-- insertion can split naturally even when the first note fills an entire page.
local function row_notes(r, first, last)
  local combined, tail
  local function collect(row, start, finish)
    for i = start, finish do
      for _, note in ipairs(row.notes[i] or {}) do
        if not combined then
          combined = note
        else
          local boundary = node.new("penalty")
          tail.next, boundary.prev = boundary, tail
          boundary.next, note.head.prev = note.head, boundary
          combined.height = combined.height + note.height
        end
        tail = node.tail(note.head)
        if note ~= combined then
          note.head = nil
          node.free(note)
        end
      end
    end
  end
  if r == 2 and first == 1 then collect(rows[1], 1, #rows[1].lines) end
  collect(rows[r], first, last)
  if combined then node.write(combined) end
end

function tables.write(r, first, last)
  local header = headers[#headers]
  if r == 1 then
    node.write(node.copy(header))
    row_notes(r, first, last)
    return
  end
  local slot = node.new(vlist)
  slot.width, slot.height = header.width, header.height
  node.set_attribute(slot, header_slot, #headers)
  local tail = slot
  for i = first, last do
    tail.next = rows[r].lines[i]
    tail.next.prev, tail = tail, tail.next
  end
  local box = node.vpack(slot)
  -- Charge the entire row to the page goal, including its final descenders.
  box.height, box.depth = box.height + box.depth, 0
  node.write(box)
  row_notes(r, first, last)
end

function tables.paginate(note_gap)
  local height = tex.dimen.textheight
  for _, row in ipairs(rows) do
    row.height, row.note_height = 0, 0
    for i, line in ipairs(row.lines) do
      local box = strip(line)
      row.lines[i] = box
      row.height = row.height + box.height + box.depth
      for _, n in ipairs(row.notes[i] or {}) do row.note_height = row.note_height + n.height end
    end
  end
  local head, tail
  for _, line in ipairs(rows[1].lines) do head, tail = append(head, tail, line) end
  local rule = node.new("rule")
  rule.width, rule.height, rule.depth = tex.hsize, tex.dimen.arrayrulewidth, 0
  head, tail = append(head, tail, rule)
  local header = node.vpack(head)
  headers[#headers + 1] = header
  local room = height - header.height
  if room < 0 then book.layout_error("table_height") end
  emit("\\par\\ifdim\\pagetotal>0pt\\vskip\\parskip\\fi\\nointerlineskip")
  local function entry(r, first, last)
    if r > 2 or first > 1 then
      emit(string.format("\\penalty0\\vskip-%dsp\\relax", header.height))
    end
    emit(string.format("\\directlua{book.tables.write(%d,%d,%d)}", r, first, last))
  end
  if #rows == 1 then entry(1, 1, #rows[1].lines) end
  for r = 2, #rows do
    local row = rows[r]
    local notes = row.note_height + (r == 2 and rows[1].note_height or 0)
    if notes > 0 then notes = notes + note_gap end
    if row.height + notes <= room then
      entry(r, 1, #row.lines)
    else
      for i, line in ipairs(row.lines) do
        if line.height + line.depth > room then book.layout_error("table_height") end
        entry(r, i, i)
      end
    end
  end
  emit("\\nointerlineskip")
end

return tables
