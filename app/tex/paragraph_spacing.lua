-- Read only the main vertical list after the page builder chose its break.
-- Footnote insertions and the running furniture are still separate here.
local hlist, vlist, rule = node.id("hlist"), node.id("vlist"), node.id("rule")
local glue, kern = node.id("glue"), node.id("kern")
local block_id = luatexbase.new_attribute("jjokgyeol.spacing_block")
local row_lines = luatexbase.new_attribute("jjokgyeol.spacing_table_lines")

return function(boundaries, default_gap)
  local spacing = {}
  local page, last_page = 0, nil
  local blocks, frame_lines = {}, {}
  local valid = true

  function spacing.begin_block(index)
    local boundary = boundaries[tostring(index)]
    local gap = boundary and boundary.gap_sp or default_gap
    tex.sprint(string.format("\\attribute%d=%d\\relax\\parskip=%dsp\\relax", block_id, index, gap))
  end

  function spacing.table_unit(box, lines)
    node.set_attribute(box, block_id, tex.getattribute(block_id))
    node.set_attribute(box, row_lines, lines)
  end

  function spacing.measure_frame(box)
    local function lines(head)
      local count = 0
      for n in node.traverse(head) do
        if n.id == hlist or n.id == vlist then
          local inner = lines(n.head)
          count = count + (inner == 0 and n.id == hlist and n.subtype == 1 and 1 or inner)
        end
      end
      return count
    end
    frame_lines[tex.getattribute(block_id)] = math.max(1, lines(box.head))
  end

  function spacing.printed_header(header)
    local info = blocks[node.has_attribute(header, block_id)]
    if info then info.lines = info.lines + node.has_attribute(header, row_lines) end
  end

  function spacing.column()
    page = page + 1
    local box = tex.box[255]
    local used, boundary_glue = 0, 0
    local has_content = false
    for n in node.traverse(box.head) do
      if n.id == glue then
        local amount = math.round(node.effective_glue(n, box))
        used = used + amount
        -- Ordinary paragraphs get parskip (3); the table uses an explicit vskip (0).
        if n.subtype == 3 or n.subtype == 0 then boundary_glue = boundary_glue + amount end
      elseif n.id == kern then
        used = used + n.kern
      elseif n.id == hlist or n.id == vlist or n.id == rule then
        local id = node.has_attribute(n, block_id)
        if id and id > 0 then
          local info = blocks[id]
          if not info then
            info = {page=page, lines=0, remaining_sp=math.max(0, box.height - used),
                    left_page=last_page, gap_sp=last_page == page and boundary_glue or 0}
            blocks[id] = info
          end
          info.lines = info.lines + (node.has_attribute(n, row_lines) or frame_lines[id] or 1)
          last_page = page
        end
        has_content = true
        used, boundary_glue = used + n.height + n.depth, 0
        if used > box.height + box.depth + 1 then valid = false end
      end
    end
    if not has_content and not tex.box.footins then valid = false end
  end

  function spacing.finish()
    local rows = {}
    for key, boundary in pairs(boundaries) do
      local id = tonumber(key)
      local info = blocks[id]
      if info and info.left_page then
        boundary.page, boundary.left_page = info.page, info.left_page
        boundary.gap_sp, boundary.lines = info.gap_sp, info.lines
        boundary.remaining_sp = info.remaining_sp
        boundary.valid = valid and (boundary.left_kind ~= "heading" or info.left_page == info.page)
        rows[#rows + 1] = boundary
      end
    end
    table.sort(rows, function(a, b) return tonumber(a.id) < tonumber(b.id) end)
    local file = assert(io.open("spacing.json", "w"))
    assert(file:write(#rows > 0 and utilities.json.tostring(rows) or "[]"))
    assert(file:close())
  end

  tex.sprint("\\AddToHook{cmd/@makecol/before}{\\directlua{book.spacing.column()}}" ..
             "\\AddToHook{enddocument/afterlastpage}{\\directlua{book.spacing.finish()}}")
  return spacing
end
