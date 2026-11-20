-- Tag broken manuscript lines, then inspect the page box that TeX actually ships.
local running = {}
local emit = tex.sprint
local first_attr = luatexbase.new_attribute("jjokgyeol.first_section")
local last_attr = luatexbase.new_attribute("jjokgyeol.last_section")
local hlist, vlist, glyph = node.id("hlist"), node.id("vlist"), node.id("glyph")
local scopes, defaults, style, literal, appearance
local width, left_at_page
local current, previous, page_style, page = 1, 1, nil, 0
local used_first = {}
local roles = {"header", "footer"}
local slots = {"left", "center", "right"}
local boxes = {"BookHeadLeft", "BookHeadCenter", "BookHeadRight"}

function running.setup(settings, book_style, body_width, page_left)
  scopes, defaults, style = settings.scopes, settings.defaults, book_style
  width, left_at_page = body_width, page_left
  for _, name in ipairs(boxes) do emit("\\newbox\\" .. name) end
  emit("\\AddToHook{shipout/before}{\\directlua{book.running.select(tex.box.ShipoutBox)}}")
  emit("\\AddToHook{shipout/foreground}{\\directlua{book.running.print()}}")
end

function running.begin(write_text, apply_face)
  literal, appearance = write_text, apply_face
  -- Only the explicit paragraph end of a manuscript block marks lines.
  -- The nested paragraph that builds a footnote has already finished here.
  emit("\\renewcommand\\BookPar{\\directlua{book.running.in_par=true}\\par\\directlua{book.running.in_par=false}}")
end

function running.section(id)
  current = id
  tex.setattribute("global", first_attr, id)
end

function running.lines(head)
  for line in node.traverse_id(hlist, head) do
    local first, last
    for c in node.traverse_id(glyph, line.head) do
      local id = node.has_attribute(c, first_attr)
      first, last = first or id, id or last
    end
    node.set_attribute(line, first_attr, first or current)
    node.set_attribute(line, last_attr, last or current)
  end
end

local function overlay(area, fields)
  if fields then
    for key, value in pairs(fields) do area[key] = value end
  end
end

function running.select(box)
  local first, last
  local function visit(head)
    for n in node.traverse(head) do
      if n.id == hlist or n.id == vlist then
        local ending = node.has_attribute(n, last_attr)
        if ending then
          first, last = first or node.has_attribute(n, first_attr), ending
        else
          visit(n.head)
        end
      end
    end
  end
  visit(box.head)
  page = page + 1
  local parity = page % 2 == 0 and "even" or "odd"
  page_style = {}
  for _, id in ipairs(scopes[first or previous].chain) do
    local scope = scopes[id]
    local first_face = (id == 1 and page == 1) or (id ~= 1 and first ~= nil and not used_first[id])
    for _, role in ipairs(roles) do
      local faces = scope[role]
      local opening = first_face and faces.first
      if faces.default or faces[parity] or opening then
        local area = page_style[role]
        if not area then
          area = {}
          overlay(area, defaults)
          page_style[role] = area
        end
        overlay(area, faces.default)
        overlay(area, faces[parity])
        overlay(area, opening)
      end
    end
    -- Footnote-only pages follow the last manuscript line but cannot start a scope.
    if first then used_first[id] = true end
  end
  previous = last or previous
end

local function no_room(role)
  local reason = string.format("running:%s:%d", role, page)
  local file = assert(io.open("layout-error.txt", "wb"))
  assert(file:write(reason))
  assert(file:close())
  error(reason)
end

function running.print()
  for _, role in ipairs(roles) do
    local area = page_style[role]
    if area and area.visible then
      for i, slot in ipairs(slots) do
        if #area[slot] > 0 then
          emit("\\setbox\\" .. boxes[i] .. "=\\hbox{{\\normalfont")
          appearance(area)
          local text = {}
          for _, token in ipairs(area[slot]) do
            text[#text + 1] = token[1] == "page" and tostring(page) or token[2]
          end
          literal(table.concat(text), true)
          emit("}}")
        end
      end
      emit("\\directlua{book.running.place('" .. role .. "')}")
    end
  end
end

function running.place(role)
  local area, m = page_style[role], style.margins_sp
  local left = left_at_page(page)
  local height, depth, right = 0, 0, 0
  local positions = {}
  for i, slot in ipairs(slots) do
    if #area[slot] > 0 then
      local box = tex.box[boxes[i]]
      -- TeX box dimensions can wrap on a very long literal. Sum shaped
      -- advances in a Lua integer before using the result as a TeX dimension.
      local w = 0
      for n in node.traverse(box.head) do w = w + node.dimensions(n, n.next) end
      local x = i == 1 and 0 or i == 2 and (width - w) // 2 or width - w
      if x < right or x + w > width then no_room(role) end
      right, positions[i] = x + w, left + x
      height, depth = math.max(height, box.height), math.max(depth, box.depth)
    end
  end
  if not next(positions) then return end
  local offset = area.offset_sp
  local header = role == "header"
  if offset < 0 or offset + height + depth > m[header and 1 or 3] then no_room(role) end
  local y = header and -offset - height or offset + depth - style.page_sp[2]
  for i, x in pairs(positions) do
    emit(string.format("\\put(%dsp,%dsp){\\box\\%s}", x, y, boxes[i]))
  end
end

return running
