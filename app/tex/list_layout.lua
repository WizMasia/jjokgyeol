-- Each distinct literal label is shaped once. Its saved box supplies both
-- the sibling-column measurement and the visible label on the first line.
local lists = {}
local fixed = require("fixed")
local boxes, groups = {}, {}

function lists.measure(blocks, literal, appearance)
  local seen = {}
  for index, block in ipairs(blocks) do
    local mark = block.mark
    if mark and not seen[block.mark_id] then
      seen[block.mark_id] = true
      -- End the label's color group before closing the box so its saved nodes
      -- contain both the PDF color push and pop on every reuse.
      tex.sprint("\\setbox\\BookListMark=\\hbox{{")
      appearance(block.overrides)
      literal(mark, true)
      tex.sprint("}}\\directlua{book.capture_mark(" .. index .. ")}")
    end
  end
end

function lists.capture(block, style, available)
  local box = tex.box.BookListMark
  if box.head then
    local shaped = fixed.prepare(node.copy_list(box.head), style)
    -- TeX's packed width can wrap. Measure each shaped node in TeX, then
    -- accumulate in a Lua integer before accepting a box dimension.
    local width = 0
    for n in node.traverse(shaped) do
      width = width + node.dimensions(n, n.next)
    end
    if width >= available or width < -available then
      require("wordbreak").no_room(block.level)
    end
    boxes[block.mark_id] = node.hpack(shaped)
  else
    boxes[block.mark_id] = node.copy(box)
  end
end

function lists.columns(blocks, available)
  for _, b in ipairs(blocks) do
    if b.kind == "list_item" then
      local group = groups[b.group]
      if not group then
        group = {width = 0, level = b.level, parent = b.parent_group,
                 indent = b.indent_sp, gap = b.gap_sp}
        groups[b.group] = group
      end
      if b.mark then group.width = math.max(group.width, boxes[b.mark_id].width) end
    end
  end
  local function resolve(group)
    if group.origin then return group.origin end
    local parent = group.parent and resolve(groups[group.parent]) or 0
    group.offset = group.width + group.gap
    group.origin = parent + group.indent + group.offset
    if group.origin >= available then require("wordbreak").no_room(group.level) end
    return group.origin
  end
  for _, group in pairs(groups) do resolve(group) end
end

function lists.origin(group)
  return groups[group].origin
end

function lists.attach(head, item)
  -- Attach to the first already-broken line so a marker cannot be stranded
  -- by the line or page builder. The zero-width outer box retains the origin.
  for line in node.traverse_id(node.id("hlist"), head) do
    local back = node.new("kern")
    back.kern = -groups[item.group].offset
    local label = node.copy(boxes[item.mark_id])
    back.next, label.prev = label, back
    local overlay = node.hpack(back)
    overlay.width = 0
    line.head = node.insert_before(line.head, line.head, overlay)
    line.height = math.max(line.height, overlay.height)
    line.depth = math.max(line.depth, overlay.depth)
    break
  end
end

return lists
