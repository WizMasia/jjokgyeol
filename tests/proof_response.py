"""Read the actual PDF files sent to a browser by the render endpoint."""
from email import policy
from email.parser import BytesParser


def rendered_pdfs(response):
    header = response.headers["content-type"]
    body = response.body()
    if header.split(";")[0] == "application/pdf":
        return {"rule": body}
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {header}\r\n\r\n".encode("ascii") + body)
    assert message.get_content_type() == "multipart/form-data"
    pdfs = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts() if part.get_content_type() == "application/pdf"}
    assert pdfs.keys() in ({"rule"}, {"rule", "model"})
    return pdfs


def selected_pdf(response, page):
    pdfs = rendered_pdfs(response)
    return pdfs[page.locator("#proof-choice").input_value()] if "model" in pdfs else pdfs["rule"]
