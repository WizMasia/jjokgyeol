-- Mark only explicit manuscript paragraphs. Assign numbers after pagination so
-- nested note paragraphs, discarded boxes and trailing notes cannot consume one.
local numbers = {}
local origin = luatexbase.new_attribute("jjokgyeol.line_origin")
local hlist, vlist, glyph = node.id("hlist"), node.id("vlist"), node.id("glyph")
local disc = node.id("disc")
local style, width, left_at_page, gap
local page, count = 0, 0

function numbers.setup(book_style, body_width, page_left, gap_pt, note_sp)
  style, width, left_at_page = book_style, body_width, page_left
  gap = tex.sp(gap_pt .. "pt")
  tex.sprint("\\AtBeginDocument{\\BookEnableLineNumbers{" .. note_sp .. "sp}}")
end

local function printed(head)
  for n in node.traverse(head) do
    if n.id == glyph or n.id == disc and n.replace and printed(n.replace) then return true end
    if (n.id == hlist or n.id == vlist) and printed(n.head) then return true end
  end
end

function numbers.mark(head)
  -- A framed title's paragraph is inset equally on both sides. The line's
  -- shift includes any first-line or hanging indent applied by parshape.
  local inset = (width - tex.hsize) // 2
  for line in node.traverse_id(hlist, head) do
    if printed(line.head) then node.set_attribute(line, origin, inset + line.shift) end
  end
end

local function label(line, offset, margin, side)
  local move = node.new("kern")
  local tail, digits, digit_width = move, tostring(count), 0
  for i = 1, #digits do
    local digit = node.new(glyph)
    digit.font, digit.char = numbers.font, digits:byte(i)
    tail.next, digit.prev = digit, tail
    tail = digit
    digit_width = digit_width + digit.width
  end
  if gap + digit_width > margin then book.layout_error("line_numbers:" .. page) end
  move.kern = (side == "left" and -gap - digit_width or width + gap) - offset
  local overlay = node.hpack(move)
  overlay.width, overlay.height, overlay.depth = 0, 0, 0
  line.head = node.insert_before(line.head, line.head, overlay)
end

function numbers.place(box)
  page = page + 1
  local side = style.line_numbers
  if type(side) == "table" then side = side[page % 2 == 1 and "odd" or "even"] end
  local left = left_at_page(page)
  local margin = side == "left" and left or style.page_sp[1] - left - width
  local function visit(head)
    for n in node.traverse(head) do
      if n.id == hlist or n.id == vlist then
        local offset = node.has_attribute(n, origin)
        if offset then
          count = count + 1
          if side ~= "off" then label(n, offset, margin, side) end
        else
          visit(n.head)
        end
      end
    end
  end
  visit(box.head)
end

return numbers
