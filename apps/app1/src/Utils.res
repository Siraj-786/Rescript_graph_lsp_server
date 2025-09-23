// src/Utils.res

// A type definition for a user record
type user = {
  id: int,
  name: string,
  email: string,
}

// A function to create a new user record
let makeUser = (id, name, email) => {
  {id: id, name: name, email: email}
}

// A function to get a user's email
let getEmail = (user: user): string => {
  user.email
}

// A function that models a user login result using a variant from Data.res
let loginUser = (isLoggedIn: bool) => {
  if isLoggedIn {
    Data.Success("User logged in successfully")
  } else {
    Data.Error(401) // Unauthorized
  }
}